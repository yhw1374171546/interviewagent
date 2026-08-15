"""
成本预算控制
============
给面试会话设 token/成本预算，超阈值自动降级——「成本控制是 Agent 岗经典题」，
兑现简历里"输入 token 减少 70%、单场成本 ≈¥0.02"的可控性承诺。

两级阈值:
    warn_threshold  — 超过后评估降级: 跳过 LLM 深度评估，用确定性关键词兜底
                     （省 token，面试继续，评分降为规则分）
    hard_threshold  — 超过后强制终止: 面试提前结束，避免成本失控

设计:
    确定性、纯状态跟踪、可测试。默认预算宽松（真实单场 ≈¥0.02，
    默认 hard 预算设为 ¥0.5 级别，正常流程绝不触发，只在异常/超长面试兜底）。

用法:
    from interview.cost_control import CostBudget
    budget = CostBudget(cost_limit_yuan=0.5, warn_ratio=0.8)
    budget.record(prompt_tokens=1000, completion_tokens=500, model="flash")
    status = budget.check()   # "normal" | "warn" | "hard"
"""

from __future__ import annotations

from config import settings


class CostBudget:
    """
    会话级成本预算跟踪。

    Args:
        cost_limit_yuan: 成本硬上限（元）。None 时用 settings 默认。
        warn_ratio: 触发 warn 的阈值比例（0.8 = 花到 80% 预算时 warn）
        token_limit: 可选 token 硬上限（同时按 token 和成本判断）
    """

    def __init__(
        self,
        cost_limit_yuan: float | None = None,
        warn_ratio: float = 0.8,
        token_limit: int | None = None,
    ):
        self.cost_limit = cost_limit_yuan if cost_limit_yuan is not None else 0.5
        self.warn_ratio = warn_ratio
        self.token_limit = token_limit or 200_000  # 默认 20 万 token 兜底
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_cost = 0.0
        self.warned = False

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens

    def record(self, prompt_tokens: int, completion_tokens: int, model: str = "") -> None:
        """记录一次 LLM 调用的用量与成本"""
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
        price = settings.llm_pricing.get(model, [0, 0])
        self.total_cost += prompt_tokens / 1_000_000 * price[0]
        self.total_cost += completion_tokens / 1_000_000 * price[1]

    def check(self) -> str:
        """
        检查预算状态。

        Returns:
            "normal" — 预算充足
            "warn"   — 超过 warn 阈值（应降级省 token）
            "hard"   — 超过硬上限（应终止）
        """
        if self.total_cost >= self.cost_limit or self.total_tokens >= self.token_limit:
            return "hard"
        if self.total_cost >= self.cost_limit * self.warn_ratio or \
                self.total_tokens >= self.token_limit * self.warn_ratio:
            self.warned = True
            return "warn"
        return "normal"

    def summary(self) -> dict:
        """预算状态摘要（可观测性）"""
        return {
            "cost_limit_yuan": self.cost_limit,
            "total_cost_yuan": round(self.total_cost, 4),
            "cost_usage_ratio": round(self.total_cost / self.cost_limit, 3),
            "total_tokens": self.total_tokens,
            "prompt_tokens": self.total_prompt_tokens,
            "completion_tokens": self.total_completion_tokens,
            "status": self.check(),
        }
