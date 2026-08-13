"""
对话摘要器
==========
当对话过长时，自动压缩历史消息为摘要，节省 Token。
"""

from __future__ import annotations

from core.llm import LLMClient, Message, Role


class ConversationSummarizer:
    """
    长对话摘要器。

    策略:
        1. 保留最近 N 条消息不变
        2. 更早的消息压缩为一段摘要
        3. 摘要作为 system prompt 的一部分注入

    使用:
        summarizer = ConversationSummarizer(llm)
        compressed = await summarizer.compress(messages, keep_recent=5)
    """

    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def compress(
        self,
        messages: list[Message],
        keep_recent: int = 5,
    ) -> list[Message]:
        """
        压缩消息列表。

        Args:
            messages: 完整消息列表
            keep_recent: 保留最近的消息数

        Returns:
            压缩后的消息列表：[summary_system_msg] + recent_messages
        """
        if len(messages) <= keep_recent:
            return messages

        to_summarize = messages[:-keep_recent]
        recent = messages[-keep_recent:]

        summary = await self._summarize(to_summarize)

        return [
            Message(
                role=Role.SYSTEM,
                content=f"[对话历史摘要]\n{summary}\n---\n以下是最近的对话:",
            ),
            *recent,
        ]

    async def _summarize(self, messages: list[Message]) -> str:
        """调用 LLM 生成摘要"""
        conv_text = "\n".join(
            f"[{m.role.value.upper()}]: {m.content[:200]}" for m in messages if m.content
        )

        prompt = (
            "请用 3-5 句话概括以下对话的关键信息。"
            "保留重要的事实、决策和用户偏好。\n\n"
            f"{conv_text}"
        )

        response = await self.llm.chat(
            messages=[Message(role=Role.USER, content=prompt)],
            temperature=0.3,
            max_tokens=500,
        )

        return response.content or "[摘要生成失败]"
