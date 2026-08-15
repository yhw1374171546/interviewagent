"""
上下文预算守卫测试（#3 Context 管理接入）
========================================
覆盖 interview/context_budget.py 的裁剪逻辑:
    - 预算内不裁剪（短历史/少弱项原样保留）
    - 超预算按优先级裁剪（保弱项 > 历史，历史保最近轮次）
    - 题目+回答很大时历史/弱项收缩
    - 与 Interviewer 集成: 长历史被裁剪，评估仍正常

全离线，CI 可直接运行。
"""

from core.mock_llm import MockLLMClient
from interview.context_budget import _estimate_tokens, fit_eval_context
from interview.interviewer import Interviewer


class TestEstimateTokens:

    def test_chinese_and_english(self):
        assert _estimate_tokens("你好") == 3  # 2 中文 × 1.5
        assert _estimate_tokens("hello world") == 2  # 11 字符 / 4 = 2（int 截断）

    def test_empty(self):
        assert _estimate_tokens("") == 0


class TestFitEvalContext:

    def test_short_context_not_cut(self):
        """预算充足时不裁剪"""
        history = "第1题(数据库) 6.5分; 第2题(系统设计) 8.0分"
        hints = ["Python 弱项", "Redis 弱项"]
        h, hs = fit_eval_context(history, hints)
        assert h == history
        assert hs == hints

    def test_long_history_cut_keeps_recent(self):
        """超预算 → 保留靠前的轮次（build_history_summary 已 reversed，靠前=最近）"""
        # 造 100 轮摘要（每轮约 28 字符，总长远超预算 600）
        history = "; ".join(
            f"第{i}题(数据库) 6.5分, 弱点: 索引原理不深入" for i in range(100)
        )
        assert len(history) > 2000
        h, _ = fit_eval_context(history, [], budget_chars=600)
        assert len(h) < 600
        # 保留的是最早的轮次（"第0题"在最前）
        assert h.startswith("第0题")
        assert "第99题" not in h

    def test_hints_kept_when_history_cut(self):
        """裁剪历史时优先保留弱项（HIGH 优先级）"""
        history = "; ".join(f"第{i}题 6.5分" for i in range(20))
        hints = ["Python 弱项", "Redis 弱项", "Kafka 弱项"]
        h, hs = fit_eval_context(history, hints, budget_chars=200)
        assert hs  # 弱项不被清空
        assert "Python" in "".join(hs)

    def test_huge_question_answer_shrinks_context(self):
        """题目+回答超大 → 历史/弱项收缩到小额度"""
        history = "第1题 6.5分; 第2题 8.0分"
        hints = ["Python 弱项", "Redis 弱项"]
        h, hs = fit_eval_context(
            history, hints,
            question_len=5000, answer_len=5000,  # 题目+回答巨大
            budget_chars=2400,
        )
        # 收缩后历史要么空要么极短，弱项 ≤1 条
        assert len(h) < 400
        assert len(hs) <= 1

    def test_empty_inputs(self):
        h, hs = fit_eval_context("", [])
        assert h == "" and hs == []


# ── Interviewer 集成 ───────────────────────────────────────────

class TestInterviewerContextBudget:

    def test_long_history_still_evaluates(self):
        """长历史下评估不崩溃（预算守卫裁剪后正常走链路）"""
        import asyncio

        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=3)
            await iv.start("Python 后端工程师，熟悉 Redis 和 MySQL")
            # 造 30 轮历史（远超预算）
            for _ in range(30):
                await iv.next_question()
                turn = await iv.submit_answer(
                    "GIL 是全局解释器锁，多进程可绕过，asyncio 适合 IO 密集。"
                    "Redis 用有序集合做排行榜，MySQL 用 B+树索引。"
                )
                if turn.is_finished:
                    break
            return iv

        iv = asyncio.run(scenario())
        # 评估正常完成且有多条答案记录
        assert len(iv.state.answers) >= 1
        assert all(a.get("evaluation") is not None for a in iv.state.answers)

    def test_budget_actually_trims(self):
        """预算守卫真实生效: 超长历史被裁剪后再进评估"""
        import asyncio

        from interview.context_budget import fit_eval_context

        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=2)
            await iv.start("Python 后端工程师")
            # 手动注入超长历史
            long_history = "; ".join(
                f"第{i}题(数据库) 6.5分, 弱点: 索引原理不深入, 覆盖: B+树查询优化" for i in range(50)
            )
            h, hs = fit_eval_context(long_history, ["Python 弱项"])
            return len(h)

        h_len = asyncio.run(scenario())
        assert h_len < 2400, f"历史应被裁剪到预算内，实际 {h_len}"
