"""
对话上下文管理
==============
滑动窗口 + Token 估算，确保上下文不超出模型限制。
"""

from __future__ import annotations

import tiktoken

from core.llm import Message, Role


class ContextManager:
    """
    对话上下文管理器。

    功能：
        - 维护消息列表
        - Token 计数与截断
        - 自动滑动窗口

    使用:
        ctx = ContextManager(max_tokens=8000)
        ctx.add(Message(role=Role.USER, content="你好"))
        ctx.add(Message(role=Role.ASSISTANT, content="你好！"))
        print(ctx.token_count)  # Token 估算
    """

    def __init__(
        self,
        max_tokens: int = 8000,
        model: str = "gpt-4o",
        system_prompt: str = "",
    ):
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self._messages: list[Message] = []

        try:
            self._encoder = tiktoken.encoding_for_model(model)
        except KeyError:
            self._encoder = tiktoken.get_encoding("cl100k_base")

    # ── 增删改 ───────────────────────────────────────────

    def add(self, message: Message) -> None:
        """添加消息，自动触发截断"""
        self._messages.append(message)
        self._truncate()

    def add_batch(self, messages: list[Message]) -> None:
        """批量添加"""
        self._messages.extend(messages)
        self._truncate()

    def clear(self) -> None:
        """清空消息（保留 system prompt）"""
        self._messages = []

    def pop(self) -> Message | None:
        """移除最早的消息"""
        if self._messages:
            return self._messages.pop(0)
        return None

    # ── Token 相关 ───────────────────────────────────────

    def count_tokens(self, text: str) -> int:
        """估算文本的 Token 数"""
        return len(self._encoder.encode(text))

    @property
    def token_count(self) -> int:
        """当前总 Token 数（含 system prompt）"""
        total = self.count_tokens(self.system_prompt)
        for msg in self._messages:
            total += 6  # 消息边框 token
            total += self.count_tokens(msg.content)
            if msg.tool_calls:
                total += self.count_tokens(str(msg.tool_calls))
        return total

    # ── 消息获取 ─────────────────────────────────────────

    @property
    def messages(self) -> list[Message]:
        """以 API 格式返回消息列表"""
        result = []
        if self.system_prompt:
            result.append(Message(role=Role.SYSTEM, content=self.system_prompt))
        result.extend(self._messages)
        return result

    @property
    def recent(self) -> list[Message]:
        """最近 10 条消息"""
        return self._messages[-10:]

    def __len__(self) -> int:
        return len(self._messages)

    def __iter__(self):
        return iter(self._messages)

    # ── Internal ─────────────────────────────────────────

    def _truncate(self) -> None:
        """超出 Token 限制时，移除最早的消息"""
        while self.token_count > self.max_tokens and len(self._messages) > 1:
            self._messages.pop(0)
