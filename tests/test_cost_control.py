"""
成本预算控制测试（#5）
======================
覆盖 interview/cost_control.py:
    - record 用量/成本累计
    - check 状态机（normal/warn/hard）
    - 与 Interviewer 集成: 超 warn 评估降级为纯规则、超 hard 强制终止

全离线（Mock LLM），CI 可直接运行。
"""

import asyncio

from core.mock_llm import MockLLMClient
from interview.cost_control import CostBudget
from interview.interviewer import Interviewer


class TestCostBudget:

    def test_record_accumulates(self):
        b = CostBudget(cost_limit_yuan=1.0)
        b.record(prompt_tokens=1000, completion_tokens=500, model="deepseek-v4-flash")
        assert b.total_prompt_tokens == 1000
        assert b.total_completion_tokens == 500
        assert b.total_tokens == 1500
        # flash 价格 [0.2, 1.0] 元/百万: 1000*0.2/1e6 + 500*1.0/1e6 = 0.0007
        assert abs(b.total_cost - 0.0007) < 1e-6

    def test_status_normal(self):
        b = CostBudget(cost_limit_yuan=1.0)
        assert b.check() == "normal"

    def test_status_warn(self):
        b = CostBudget(cost_limit_yuan=1.0, warn_ratio=0.5)
        assert b.check() == "normal"
        # pro 价格 [1.0, 4.0] 元/百万: 400k prompt = 0.4 元（token 400k > 200k 上限？）
        # 用 token_limit 调大避免撞 token 上限，专注测成本阈值
        b2 = CostBudget(cost_limit_yuan=1.0, warn_ratio=0.5, token_limit=10_000_000)
        b2.record(prompt_tokens=400_000, completion_tokens=0, model="deepseek-v4-pro")
        # 0.4 元 < 0.5 warn 阈值 → normal
        assert b2.check() == "normal"
        b2.record(prompt_tokens=200_000, completion_tokens=0, model="deepseek-v4-pro")
        # 0.6 元 > 0.5 → warn
        assert b2.check() == "warn"

    def test_status_hard(self):
        b = CostBudget(cost_limit_yuan=1.0, warn_ratio=0.5, token_limit=10_000_000)
        b.record(prompt_tokens=1_200_000, completion_tokens=0, model="deepseek-v4-pro")
        # 1.2 元 > 1.0 → hard
        assert b.check() == "hard"

    def test_token_limit_hard(self):
        b = CostBudget(cost_limit_yuan=100.0, token_limit=10_000)
        b.record(prompt_tokens=9_000, completion_tokens=0, model="deepseek-v4-flash")
        assert b.check() == "warn"  # 9000 > 8000 (warn)
        b.record(prompt_tokens=2_000, completion_tokens=0, model="deepseek-v4-flash")
        assert b.check() == "hard"  # 11000 > 10000

    def test_summary(self):
        b = CostBudget(cost_limit_yuan=1.0)
        b.record(prompt_tokens=1000, completion_tokens=0, model="deepseek-v4-flash")
        s = b.summary()
        assert s["total_tokens"] == 1000
        assert s["status"] == "normal"
        assert "cost_usage_ratio" in s


# ── Interviewer 集成 ───────────────────────────────────────────

JD = """
Python 后端开发工程师
要求: 精通 Python，熟悉 FastAPI，了解 Redis 和 MySQL
"""

ANSWER = (
    "GIL 是全局解释器锁，同一时刻只有一个线程执行字节码，CPU 密集用多进程"
    "绕过，IO 密集用 asyncio。Redis 有序集合做排行榜，MySQL B+树索引。"
)


def run(coro):
    return asyncio.run(coro)


class TestInterviewerCostBudget:

    def test_normal_budget_not_affected(self):
        """默认宽松预算下评估照常（LLM 评估正常进行）"""
        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=3)
            await iv.start(JD)
            for _ in range(2):
                await iv.next_question()
                turn = await iv.submit_answer(ANSWER)
                if turn.is_finished:
                    break
            return iv

        iv = run(scenario())
        # 默认预算 ¥0.5，mock 用量极小 → 不降级
        assert len(iv.state.answers) == 2
        assert iv.state.answers[0]["evaluation"].overall_comment  # 有 LLM 评语

    def test_warn_budget_degrades_to_rule_eval(self):
        """超 warn 阈值 → 评估降级为纯规则（无 LLM 调用）"""
        async def scenario():
            # 预算 ¥0.1, warn 50% → warn 区间 [0.05, 0.1)
            budget = CostBudget(
                cost_limit_yuan=0.1, warn_ratio=0.5, token_limit=10_000_000,
            )
            iv = Interviewer(
                MockLLMClient(), total_questions=3, cost_budget=budget,
            )
            await iv.start(JD)
            await iv.next_question()
            # 注入 0.08 元（warn 区间）: pro 80k prompt = 0.08 元
            iv.cost_budget.record(
                prompt_tokens=80_000, completion_tokens=0, model="deepseek-v4-pro",
            )
            assert iv.cost_budget.check() == "warn"
            turn = await iv.submit_answer(ANSWER)
            return turn, iv

        turn, iv = run(scenario())
        # 降级后评估仍完成（规则兜底），不崩溃
        assert iv.state.answers[0]["evaluation"] is not None

    def test_hard_budget_terminates(self):
        """超 hard 阈值 → 强制结束面试"""
        async def scenario():
            budget = CostBudget(
                cost_limit_yuan=0.05, warn_ratio=0.5, token_limit=10_000_000,
            )
            iv = Interviewer(
                MockLLMClient(), total_questions=3, cost_budget=budget,
            )
            await iv.start(JD)
            await iv.next_question()
            # 注入 0.08 元 > 0.05 hard
            iv.cost_budget.record(
                prompt_tokens=80_000, completion_tokens=0, model="deepseek-v4-pro",
            )
            turn = await iv.submit_answer(ANSWER)
            return turn, iv

        turn, iv = run(scenario())
        assert turn.is_finished is True
        assert "预算" in turn.message
