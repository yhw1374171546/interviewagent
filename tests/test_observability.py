"""
可观测性测试（2b）
=================
验证调用级指标收集: LLM 用量统计、会话级阶段指标、成本估算。
全部离线（Mock LLM），CI 可直接运行。
"""

import asyncio

from core.mock_llm import MockLLMClient
from interview.interviewer import Interviewer


def run(coro):
    return asyncio.run(coro)


class TestLLMUsageStats:

    def test_chat_with_retry_records_usage(self):
        """每次 chat_with_retry 调用都应累计延迟与 token"""
        llm = MockLLMClient()

        async def scenario():
            await llm.chat_with_retry(
                [llm.user_message("开场白测试")], max_tokens=100,
            )
            return llm

        llm = run(scenario())
        assert llm.usage_stats["call_count"] == 1
        assert llm.usage_stats["prompt_tokens"] > 0
        assert llm.usage_stats["total_latency_sec"] >= 0

    def test_reset_usage_stats(self):
        llm = MockLLMClient()
        llm.usage_stats["call_count"] = 5
        llm.reset_usage_stats()
        assert llm.usage_stats["call_count"] == 0


class TestSessionMetrics:

    def test_full_interview_records_stage_metrics(self):
        """完整面试后，各阶段指标都应存在且 token 非零"""
        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=2)
            await iv.run_full_interview(
                "Python 后端工程师，要求 FastAPI MySQL",
                answers=[
                    "MySQL 索引用 B+ 树实现，减少磁盘 IO，支持范围查询。",
                    "Redis 单线程事件循环和 IO 多路复用实现高性能。",
                ],
            )
            return iv

        iv = run(scenario())
        assert set(iv.state.metrics) >= {"jd_parse", "question_gen+warmup", "evaluate", "report"}
        # 各阶段 token 之和 > 0（mock 按字符数/4 估算）
        total_prompt = sum(m.get("prompt_tokens", 0) for m in iv.state.metrics.values())
        assert total_prompt > 0

    def test_cost_estimate(self):
        """成本估算: token 与价格表相乘，非负且结构完整"""
        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=1)
            await iv.run_full_interview(
                "Python 后端工程师", answers=["FastAPI 是异步 Web 框架。"],
            )
            return iv.session_cost_estimate()

        cost = run(scenario())
        assert cost["prompt_tokens"] > 0
        assert cost["completion_tokens"] > 0
        assert cost["cost_yuan"] >= 0

    def test_metrics_survive_serialization(self):
        """指标随状态快照持久化（断点恢复后仍可查）"""
        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=1)
            await iv.run_full_interview(
                "Python 后端工程师", answers=["FastAPI 是异步 Web 框架。"],
            )
            return iv

        iv = run(scenario())
        restored = Interviewer.from_dict(iv.to_dict(), MockLLMClient())
        assert restored.state.metrics == iv.state.metrics
        assert restored.state.timings == iv.state.timings

    def test_from_dict_accepts_llm_strong(self):
        """回归: from_dict 必须接受 llm_strong 参数（web 断点恢复时传入强模型）"""
        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=1)
            await iv.run_full_interview("Python 后端工程师", answers=["FastAPI。"])
            return iv

        iv = run(scenario())
        strong = MockLLMClient()
        restored = Interviewer.from_dict(
            iv.to_dict(), MockLLMClient(), memory=None, llm_strong=strong,
        )
        assert restored.llm_strong is strong

    def test_resume_after_restart_continues_interview(self):
        """B1 断点恢复: 中途序列化 → from_dict 重建 → 继续答题不丢进度（刷新不丢题）"""
        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=3)
            await iv.start("Python 后端工程师")
            await iv.next_question()
            await iv.submit_answer("FastAPI 是基于 Starlette 的异步 Web 框架。")
            snapshot = iv.to_dict()  # 磁盘持久化快照（服务重启前的等价物）

            # 模拟服务重启: 全新 Interviewer 从快照重建，进度/回答不丢
            restored = Interviewer.from_dict(snapshot, MockLLMClient())
            assert len(restored.state.answers) == 1
            assert restored.state.current_question_index == iv.state.current_question_index
            assert not restored.state.is_finished  # 未结束 → Web can_resume=true

            # 恢复后继续提交回答（刷新页面后的下一次作答）
            turn = await restored.submit_answer("继续回答下一题。")
            assert len(restored.state.answers) == 2
            assert turn is not None
            return restored

        restored = run(scenario())
        assert restored.state.phase.value in ("follow_up", "question", "finished")

    def test_to_dict_is_json_serializable(self):
        """快照必须能 json.dumps（Web persist 落盘依赖）— 防 set 序列化回归"""
        import json

        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=2, adaptive_enabled=True)
            await iv.start("Python 后端工程师")
            await iv.next_question()
            await iv.submit_answer("FastAPI 是基于 Starlette 的异步 Web 框架。")
            return iv

        iv = run(scenario())
        text = json.dumps(iv.to_dict(), ensure_ascii=False)  # 曾因 set 抛 TypeError
        assert "adaptive_used_ids" in text

    def test_from_dict_restores_adaptive_state(self):
        """断点恢复后自适应状态不丢（set 经 JSON 序列化为 list 后正确还原）"""
        import json

        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=2, adaptive_enabled=True)
            await iv.start("Python 后端工程师")
            await iv.next_question()
            await iv.submit_answer("FastAPI 是基于 Starlette 的异步 Web 框架。")
            return iv

        iv = run(scenario())
        # 模拟磁盘往返（JSON 序列化 → 反序列化）
        data = json.loads(json.dumps(iv.to_dict(), ensure_ascii=False))
        restored = Interviewer.from_dict(data, MockLLMClient())
        assert restored.state.adaptive_enabled == iv.state.adaptive_enabled
        assert isinstance(restored.state.adaptive_used_ids, set)
        assert restored.state.adaptive_used_ids == iv.state.adaptive_used_ids
