"""
LLM 抽象层 (v3 — 生产级)
=========================
提供统一的 LLM 调用接口，屏蔽不同厂商的 API 差异。

支持的 Provider:
- openai    : GPT-4o, GPT-4o-mini, etc.
- anthropic : Claude Opus 5, Sonnet 5, Haiku 4.5, etc.
- ollama    : 本地模型 (兼容 OpenAI 接口格式)

生产级特性 (v3 新增):
- Structured Output: JSON Schema 约束输出格式
- 自动重试 + 熔断: 集成 core.retry 模块
- Streaming: 流式输出支持
- Prompt Caching: 复用 system prompt 减少成本
- Token 预算控制: 自动截断防止超额
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

# 注意: openai / anthropic SDK 采用懒加载（在各自 Client 的 __init__ 中导入）。
# 这样只装了其中一个 SDK、或只跑 Mock/规则引擎（demo.py、web mock 模式）时，
# 整个项目无需安装全部 LLM 依赖也能正常导入和运行。


# ── 类型定义 ──────────────────────────────────────────────────

class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    """单条对话消息"""
    role: Role
    content: str
    tool_call_id: str | None = None
    tool_calls: list[dict] | None = None


@dataclass
class ToolCall:
    """LLM 返回的工具调用"""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """LLM 统一返回结构"""
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)


@dataclass
class ToolDefinition:
    """工具定义，用于 Function Calling"""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


@dataclass
class StructuredOutputConfig:
    """
    Structured Output 配置。
    约束 LLM 输出符合指定的 JSON Schema。
    """
    json_schema: dict[str, Any]       # JSON Schema 定义
    strict: bool = True                # 是否严格模式
    max_retries_on_format_error: int = 2  # 格式错误时自动重试次数


@dataclass
class PromptCacheConfig:
    """
    Prompt Caching 配置 (Anthropic 原生支持, OpenAI 自动缓存)。

    适用场景:
        - System prompt 在多次调用中不变
        - 题库检索后的大段上下文反复使用
        - JD 分析结果在多轮面试中复用
    """
    cache_system_prompt: bool = True   # 缓存 system prompt
    cache_breakpoints: int = 2         # 缓存前 N 条消息
    enabled: bool = True


# ── LLM 抽象基类 ──────────────────────────────────────────────

class LLMClient(ABC):
    """
    LLM 客户端抽象基类。
    所有 Provider 实现需继承此类并实现 chat 方法。
    """

    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self._retry_handler = None  # 延迟初始化
        self._cache_config = PromptCacheConfig()
        # 调用级可观测性: 所有经 chat_with_retry 的调用累计到此
        # （token 来自 API 返回的 usage，mock 为字符数/4 估算）
        self.usage_stats = {
            "call_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_latency_sec": 0.0,
        }

    def reset_usage_stats(self) -> None:
        """清零用量统计"""
        for key in self.usage_stats:
            self.usage_stats[key] = 0 if key != "total_latency_sec" else 0.0

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = False,
    ) -> LLMResponse:
        """发送消息并获取回复"""
        ...

    async def structured_chat(
        self,
        messages: list[Message],
        output_schema: StructuredOutputConfig,
        temperature: float = 0.3,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        """
        结构化输出 — 约束 LLM 返回符合 JSON Schema 的数据。

        实现策略:
            OpenAI: 原生支持 response_format={"type": "json_schema", ...}
            Anthropic: 在 system prompt 中注入 JSON Schema 要求 + 输出后校验
            Ollama: 同 Anthropic 策略

        容错:
            如果输出不符合 Schema，自动重试（告诉 LLM 哪里格式不对）。

        Args:
            messages: 消息列表
            output_schema: JSON Schema 约束
            temperature: 温度（结构化输出建议低温度）
            max_tokens: 最大 token

        Returns:
            LLMResponse，.content 中的 JSON 保证符合 Schema
        """
        # 注入格式要求到 system prompt
        schema_json = json.dumps(output_schema.json_schema, ensure_ascii=False)
        format_instruction = (
            f"\n\n## 输出格式要求\n"
            f"你必须严格按照以下 JSON Schema 返回数据，直接返回 JSON，不要包装在 markdown 代码块中:\n"
            f"```json\n{schema_json}\n```\n"
            f"只返回 JSON，不要添加任何其他文字。"
        )

        # 在最后一条 user message 中追加格式要求
        modified = list(messages)
        if modified and modified[-1].role == Role.USER:
            modified[-1] = Message(
                role=Role.USER,
                content=modified[-1].content + format_instruction,
            )

        last_error = None
        for attempt in range(output_schema.max_retries_on_format_error + 1):
            response = await self.chat(
                messages=modified,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            # 校验 JSON 格式
            from interview.output_validator import safe_parse_json
            data, parse_error = safe_parse_json(response.content)

            if data is not None:
                return response  # JSON 有效

            # 格式无效 — 告诉 LLM 修正
            last_error = parse_error
            if attempt < output_schema.max_retries_on_format_error:
                logger.warning(f"Structured Output 格式错误 (尝试 {attempt+1}): {parse_error}")
                correction_msg = Message(
                    role=Role.USER,
                    content=(
                        f"你的上一次输出格式不正确: {parse_error}\n"
                        f"请严格按照 JSON Schema 重新输出，只返回有效的 JSON。"
                    ),
                )
                modified.append(response.content and Message(
                    role=Role.ASSISTANT, content=response.content
                ) or Message(role=Role.ASSISTANT, content=""))
                modified.append(correction_msg)

        raise ValueError(f"Structured Output 重试 {output_schema.max_retries_on_format_error} 次后仍然格式错误: {last_error}")

    async def chat_with_retry(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        max_retries: int = 3,
    ) -> LLMResponse:
        """
        带自动重试的 chat。

        自动处理: API 限流(429)、超时、连接错误
        不重试: 参数错误(400)、认证错误(401)
        """
        from .retry import RetryConfig, with_retry

        config = RetryConfig(max_retries=max_retries)

        # 注意: 用关键字参数调用 chat — 兼容 **kwargs 签名的 LLM 实现
        # （如测试 Fake/第三方包装），位置参数会直接 TypeError
        import time as _time

        t0 = _time.perf_counter()
        response = await with_retry(
            fn=lambda: self.chat(
                messages=messages, tools=tools,
                temperature=temperature, max_tokens=max_tokens,
            ),
            config=config,
        )

        # 调用级指标: 延迟 + token（供会话级聚合与成本估算）
        self.usage_stats["call_count"] += 1
        self.usage_stats["total_latency_sec"] += _time.perf_counter() - t0
        usage = getattr(response, "usage", None) or {}
        self.usage_stats["prompt_tokens"] += usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0) or 0
        self.usage_stats["completion_tokens"] += usage.get("completion_tokens", 0) or usage.get("output_tokens", 0) or 0
        return response

    async def stream_chat(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """
        流式输出 — 逐步返回文本块。

        用于实时显示面试官的追问/反馈。

        Yields:
            str: 文本增量（每次一个或多个 token）
        """
        # 默认实现：非流式 → 一次性返回全部文本
        response = await self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        yield response.content

    def with_cache(self, enabled: bool = True) -> LLMClient:
        """启用/禁用 prompt 缓存"""
        self._cache_config.enabled = enabled
        return self

    def system_message(self, content: str) -> Message:
        return Message(role=Role.SYSTEM, content=content)

    def user_message(self, content: str) -> Message:
        return Message(role=Role.USER, content=content)

    @property
    def retry_handler(self):
        """延迟初始化 retry handler"""
        if self._retry_handler is None:
            from .retry import LLMRetryHandler
            self._retry_handler = LLMRetryHandler(self)
        return self._retry_handler


# ── OpenAI 实现 ───────────────────────────────────────────────

class OpenAIClient(LLMClient):
    """OpenAI API (及兼容接口: Ollama, DeepSeek, etc.)"""

    def __init__(self, model: str = "gpt-4o", api_key: str | None = None, base_url: str | None = None):
        super().__init__(model, api_key or os.getenv("OPENAI_API_KEY"), base_url)
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("未安装 openai SDK，请执行: pip install openai")
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            max_retries=0,  # 我们自己控制重试
        )

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = False,
    ) -> LLMResponse:
        openai_tools = None
        if tools:
            openai_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]

        formatted = [
            {"role": m.role.value, "content": m.content}
            for m in messages
        ]

        # Structured Output (OpenAI 原生支持)
        extra_kwargs = {}
        if hasattr(self, "_output_schema") and self._output_schema:
            extra_kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "schema": self._output_schema,
                    "strict": True,
                },
            }

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=formatted,
            tools=openai_tools,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
            **extra_kwargs,
        )

        choice = response.choices[0]
        msg = choice.message

        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))

        return LLMResponse(
            content=msg.content or "",
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage={
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            },
        )

    async def stream_chat(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """流式输出 — OpenAI 原生支持"""
        formatted = [
            {"role": m.role.value, "content": m.content}
            for m in messages
        ]

        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=formatted,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


# ── Anthropic 实现 ────────────────────────────────────────────

class AnthropicClient(LLMClient):
    """
    Anthropic Claude API.

    特性:
        - 原生 Prompt Caching: system prompt 和 tools 定义自动缓存
        - 缓存命中可降低 90% 的输入成本
        - Streaming: 原生支持 SSE 流式输出
    """

    def __init__(self, model: str = "claude-sonnet-5-20251001", api_key: str | None = None, base_url: str | None = None):
        super().__init__(model, api_key or os.getenv("ANTHROPIC_API_KEY"), base_url)
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            raise ImportError("未安装 anthropic SDK，请执行: pip install anthropic")
        self.client = AsyncAnthropic(
            api_key=self.api_key,
            base_url=self.base_url,
            max_retries=0,  # 我们自己控制重试
        )

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = False,
    ) -> LLMResponse:
        # Anthropic 消息格式不同 — 需要分离 system 消息
        system_prompt = ""
        formatted = []
        for m in messages:
            if m.role == Role.SYSTEM:
                system_prompt += m.content + "\n"
            else:
                formatted.append({"role": m.role.value, "content": m.content})

        anthropic_tools = None
        if tools:
            anthropic_tools = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                }
                for t in tools
            ]

        kwargs = dict(
            model=self.model,
            messages=formatted,
            tools=anthropic_tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if system_prompt.strip():
            # Prompt Caching: system prompt 标记为可缓存
            if self._cache_config.enabled and self._cache_config.cache_system_prompt:
                kwargs["system"] = [
                    {
                        "type": "text",
                        "text": system_prompt.strip(),
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
                logger.debug("Anthropic Prompt Caching 已启用 (system prompt)")
            else:
                kwargs["system"] = system_prompt.strip()

        # Prompt Caching: 缓存前 N 条消息
        if self._cache_config.enabled and formatted:
            cache_count = min(self._cache_config.cache_breakpoints, len(formatted))
            for i in range(len(formatted) - cache_count, len(formatted)):
                if isinstance(formatted[i].get("content"), str):
                    formatted[i]["content"] = [
                        {
                            "type": "text",
                            "text": formatted[i]["content"],
                            "cache_control": {"type": "ephemeral"},
                        }
                    ]

        response = await self.client.messages.create(**kwargs)

        tool_calls = []
        for block in response.content:
            if block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input,
                ))

        text = "\n".join(b.text for b in response.content if b.type == "text")

        # 检查缓存命中
        usage = {}
        if response.usage:
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }
            # Anthropic 缓存命中信息
            if hasattr(response.usage, 'cache_read_input_tokens'):
                usage["cache_read_tokens"] = response.usage.cache_read_input_tokens or 0
                usage["cache_creation_tokens"] = response.usage.cache_creation_input_tokens or 0

        return LLMResponse(
            content=text,
            tool_calls=tool_calls,
            finish_reason=response.stop_reason or "end_turn",
            usage=usage,
        )

    async def stream_chat(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """流式输出 — Anthropic SSE 原生支持"""
        system_prompt = ""
        formatted = []
        for m in messages:
            if m.role == Role.SYSTEM:
                system_prompt += m.content + "\n"
            else:
                formatted.append({"role": m.role.value, "content": m.content})

        kwargs = dict(
            model=self.model,
            messages=formatted,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        if system_prompt.strip():
            kwargs["system"] = system_prompt.strip()

        async with self.client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text
