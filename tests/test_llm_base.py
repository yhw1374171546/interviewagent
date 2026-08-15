"""
LLM 抽象基类测试（core/llm.py 覆盖率补全，D 组）
================================================
覆盖基类方法（不依赖任何 SDK，全部离线）:
    structured_chat（成功/重试/重试耗尽/格式注入）
    retry_handler 延迟初始化、with_cache、_estimate_prompt_tokens
    system_message/user_message、stream_chat 默认实现
CI 可直接运行。
"""

import asyncio

import pytest

from core.llm import (
    LLMClient,
    LLMResponse,
    Message,
    Role,
    StructuredOutputConfig,
)


def run(coro):
    return asyncio.run(coro)


class DummyLLM(LLMClient):
    """最小实现：记录调用，可配置响应队列"""

    def __init__(self, responses: list[LLMResponse] | None = None):
        super().__init__("test-model")
        self.queue = list(responses or [])
        self.calls: list[dict] = []

    async def chat(self, messages, tools=None, temperature=0.7,
                   max_tokens=4096, stream=False):
        self.calls.append({
            "messages": messages, "tools": tools,
            "temperature": temperature, "max_tokens": max_tokens,
        })
        if self.queue:
            return self.queue.pop(0)
        return LLMResponse(content="{}")


def _json_response(content: str) -> LLMResponse:
    return LLMResponse(content=content, usage={"prompt_tokens": 5, "completion_tokens": 3})


class TestStructuredChat:

    SCHEMA = StructuredOutputConfig(
        json_schema={"type": "object", "properties": {"name": {"type": "string"}}},
        max_retries_on_format_error=2,
    )

    def test_success_returns_response(self):
        llm = DummyLLM([_json_response('{"name": "ok"}')])
        resp = run(llm.structured_chat(
            [Message(role=Role.USER, content="hi")], self.SCHEMA,
        ))
        assert resp.content == '{"name": "ok"}'
        assert len(llm.calls) == 1

    def test_retries_on_bad_json(self):
        """前 2 次坏 JSON → 第 3 次合法：调用 3 次且成功"""
        llm = DummyLLM([
            _json_response("not json at all"),
            _json_response("{broken"),
            _json_response('{"name": "ok"}'),
        ])
        resp = run(llm.structured_chat(
            [Message(role=Role.USER, content="hi")], self.SCHEMA,
        ))
        assert resp.content == '{"name": "ok"}'
        assert len(llm.calls) == 3

    def test_exhausted_raises_value_error(self):
        """全部格式错误 → 重试耗尽抛 ValueError（不静默返回坏数据）"""
        llm = DummyLLM([_json_response("bad") for _ in range(3)])
        with pytest.raises(ValueError):
            run(llm.structured_chat(
                [Message(role=Role.USER, content="hi")], self.SCHEMA,
            ))

    def test_schema_injected_into_last_user_message(self):
        """格式要求追加到最后一条 user 消息（含 JSON Schema）"""
        llm = DummyLLM([_json_response('{"name": "ok"}')])
        run(llm.structured_chat(
            [Message(role=Role.USER, content="原始问题")], self.SCHEMA,
        ))
        last = llm.calls[0]["messages"][-1]
        assert last.role == Role.USER
        assert "输出格式要求" in last.content
        assert '"type": "object"' in last.content

    def test_correction_message_appended_on_retry(self):
        """格式错误重试时追加「上一条输出 + 修正指令」"""
        llm = DummyLLM([
            _json_response("bad json"),
            _json_response('{"name": "ok"}'),
        ])
        run(llm.structured_chat(
            [Message(role=Role.USER, content="hi")], self.SCHEMA,
        ))
        # 第二次调用: 原消息 + assistant 坏输出 + user 修正指令
        assert len(llm.calls[1]["messages"]) == 3
        assert llm.calls[1]["messages"][1].role == Role.ASSISTANT
        assert "格式不正确" in llm.calls[1]["messages"][2].content


class TestBaseMisc:

    def test_retry_handler_lazy_init(self):
        """retry_handler 延迟创建且缓存（同一实例）"""
        llm = DummyLLM()
        assert llm._retry_handler is None
        h1 = llm.retry_handler
        assert llm._retry_handler is not None
        assert llm.retry_handler is h1

    def test_estimate_prompt_tokens(self):
        """流式路径无 usage 时用字符数估算（与 Mock 同口径）"""
        msgs = [Message(role=Role.USER, content="a" * 40)]
        assert LLMClient._estimate_prompt_tokens(msgs) == 10

    def test_with_cache_returns_self(self):
        llm = DummyLLM()
        assert llm.with_cache(False) is llm
        assert llm._cache_config.enabled is False
        assert llm.with_cache(True)._cache_config.enabled is True

    def test_message_helpers(self):
        llm = DummyLLM()
        assert llm.system_message("sys").role == Role.SYSTEM
        assert llm.user_message("usr").role == Role.USER
        assert llm.system_message("sys").content == "sys"

    def test_stream_chat_default_non_stream(self):
        """基类默认 stream_chat = 非流式一次性返回全部文本"""
        llm = DummyLLM([_json_response("完整文本")])

        async def scenario():
            chunks = []
            async for c in llm.stream_chat([Message(role=Role.USER, content="q")]):
                chunks.append(c)
            return chunks

        assert run(scenario()) == ["完整文本"]

    def test_reset_usage_stats(self):
        llm = DummyLLM()
        llm._record_usage(10, 5, 1.5)
        assert llm.usage_stats["call_count"] == 1
        llm.reset_usage_stats()
        assert llm.usage_stats["call_count"] == 0
        assert llm.usage_stats["total_latency_sec"] == 0.0
