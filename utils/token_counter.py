"""
Token 计数器
============
精确统计 Token 用量，支持成本估算。
"""

from __future__ import annotations

import tiktoken

# 模型 Token 价格 (per 1M tokens, USD)
PRICING = {
    "gpt-4o":           {"input": 2.50,  "output": 10.00},
    "gpt-4o-mini":      {"input": 0.15,  "output": 0.60},
    "claude-sonnet-5":  {"input": 3.00,  "output": 15.00},
    "claude-haiku-4.5": {"input": 0.80,  "output": 4.00},
}


class TokenCounter:
    """
    Token 用量与成本追踪器。

    使用:
        counter = TokenCounter("gpt-4o")
        counter.count_input("Hello, world!")
        counter.count_output("Hi there!")
        print(counter.summary())
    """

    def __init__(self, model: str = "gpt-4o"):
        self.model = model
        self.input_tokens = 0
        self.output_tokens = 0

        try:
            self._encoder = tiktoken.encoding_for_model(model)
        except KeyError:
            self._encoder = tiktoken.get_encoding("cl100k_base")

    def count_input(self, text: str) -> int:
        """统计输入 Token"""
        tokens = len(self._encoder.encode(text))
        self.input_tokens += tokens
        return tokens

    def count_output(self, text: str) -> int:
        """统计输出 Token"""
        tokens = len(self._encoder.encode(text))
        self.output_tokens += tokens
        return tokens

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def estimated_cost(self) -> float:
        """估算费用 (USD)"""
        price = PRICING.get(self.model)
        if not price:
            return 0.0
        return (
            self.input_tokens / 1_000_000 * price["input"]
            + self.output_tokens / 1_000_000 * price["output"]
        )

    def summary(self) -> str:
        """用量报告"""
        return (
            f"Token 用量: 输入 {self.input_tokens:,} | 输出 {self.output_tokens:,} | "
            f"总计 {self.total_tokens:,} | 估算费用 ${self.estimated_cost:.4f}"
        )

    def reset(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
