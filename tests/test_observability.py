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
