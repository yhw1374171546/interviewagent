"""
LLM Provider 客户端测试（core/llm.py 覆盖率补全，D 组）
======================================================
用 fake SDK client 注入（monkeypatch）验证消息格式化与响应解析，
**零网络调用** — 覆盖那些真实 API 400 的修复逻辑:
    OpenAI: tool_call_id / tool_calls / reasoning_content 字段透传、
            工具定义转换、tool_calls 解析（含坏 JSON 兜底）、usage 提取
    Anthropic: system 分离、tool_result / tool_use 块、cache_control、
            响应解析（tool_use + text 混合）、流式
"""

import asyncio

import pytest

from core.llm import (
    AnthropicClient,
    Message,
    OpenAIClient,
    Role,
    ToolCall,
    ToolDefinition,
)


def run(coro):
    return asyncio.run(coro)


# ── OpenAI fake ────────────────────────────────────────────────

def _openai_response(content="", tool_calls=None, finish="stop", usage=None,
                     reasoning=None):
    """构造与 openai SDK 响应同构的鸭子类型对象"""
    class _Msg:
        pass
    class _Choice:
        pass
    class _Usage:
        pass

    msg = _Msg()
    msg.content = content
    msg.tool_calls = tool_calls
    msg.reasoning_content = reasoning
    choice = _Choice()
    choice.message = msg
    choice.finish_reason = finish
    resp = type("Resp", (), {})()
    resp.choices = [choice]
    if usage:
        u = _Usage()
        u.prompt_tokens = usage[0]
        u.completion_tokens = usage[1]
        resp.usage = u
    else:
        resp.usage = None
    return resp


def _openai_tool_call(tc_id, name, args_json):
    class _TC:
        pass
    tc = _TC()
    tc.id = tc_id
    fn = type("Fn", (), {})()
    fn.name = name
    fn.arguments = args_json
    tc.function = fn
    return tc


def _make_openai(monkeypatch, resp):
    client = OpenAIClient(model="test", api_key="k")
    recorded = {}

    class _Completions:
        async def create(self, **kwargs):
            recorded["kwargs"] = kwargs
            return resp

    class _Chat:
        completions = _Completions()

    class _Fake:
        chat = _Chat()

    monkeypatch.setattr(client, "client", _Fake())
    return client, recorded


class TestOpenAIClient:

    def test_tool_call_id_passthrough(self, monkeypatch):
        """TOOL 消息的 tool_call_id 必须透传（此前丢失导致真实 API 400）"""
        client, recorded = _make_openai(monkeypatch, _openai_response("ok"))
        run(client.chat([Message(role=Role.TOOL, content="结果", tool_call_id="call_1")]))
        msgs = recorded["kwargs"]["messages"]
        assert msgs[0]["tool_call_id"] == "call_1"
        assert msgs[0]["role"] == "tool"

    def test_tool_calls_and_reasoning_passthrough(self, monkeypatch):
        """assistant 消息的 tool_calls / reasoning_content 原样回传（thinking 400 修复）"""
        client, recorded = _make_openai(monkeypatch, _openai_response("ok"))
        run(client.chat([Message(
            role=Role.ASSISTANT, content="想一下",
            tool_calls=[{"id": "t1", "function": {"name": "f", "arguments": "{}"}}],
            reasoning_content="思维链",
        )]))
        msgs = recorded["kwargs"]["messages"]
        assert msgs[0]["tool_calls"] == [{"id": "t1", "function": {"name": "f", "arguments": "{}"}}]
        assert msgs[0]["reasoning_content"] == "思维链"

    def test_tools_converted_to_openai_format(self, monkeypatch):
        client, recorded = _make_openai(monkeypatch, _openai_response("ok"))
        run(client.chat(
            [Message(role=Role.USER, content="hi")],
            tools=[ToolDefinition(name="search", description="搜", parameters={"type": "object"})],
        ))
        tools = recorded["kwargs"]["tools"]
        assert tools == [{
            "type": "function",
            "function": {"name": "search", "description": "搜", "parameters": {"type": "object"}},
        }]

    def test_tool_calls_parsed(self, monkeypatch):
        """响应 tool_calls → ToolCall 列表；坏 JSON 兜底为 {}"""
        client, recorded = _make_openai(monkeypatch, _openai_response(
            tool_calls=[
                _openai_tool_call("c1", "add", '{"a": 1}'),
                _openai_tool_call("c2", "bad", "not-json"),
            ],
        ))
        resp = run(client.chat([Message(role=Role.USER, content="hi")]))
        assert resp.tool_calls == [
            ToolCall(id="c1", name="add", arguments={"a": 1}),
            ToolCall(id="c2", name="bad", arguments={}),
        ]

    def test_usage_and_reasoning_extracted(self, monkeypatch):
        client, recorded = _make_openai(monkeypatch, _openai_response(
            content="回答", usage=(10, 20), reasoning="思考",
        ))
        resp = run(client.chat([Message(role=Role.USER, content="q")]))
        assert resp.usage == {"prompt_tokens": 10, "completion_tokens": 20}
        assert resp.reasoning_content == "思考"
        assert resp.finish_reason == "stop"

    def test_openai_stream_aggregates_deltas(self, monkeypatch):
        client, recorded = _make_openai(monkeypatch, _openai_response())
        chunks = ["你", "好", "呀"]

        class _Delta:
            content = None

        class _Chunk:
            def __init__(self, text):
                class _C:
                    pass

                c = _C()
                c.delta = _Delta()
                c.delta.content = text
                c.choices = [c]
                self.choices = c.choices

        class _Completions:
            async def create(self, **kwargs):
                class _AI:
                    def __init__(self, texts):
                        self._texts = list(texts)

                    def __aiter__(self):
                        return self

                    async def __anext__(self):
                        if not self._texts:
                            raise StopAsyncIteration
                        return _Chunk(self._texts.pop(0))

                return _AI(chunks)

        class _Chat:
            completions = _Completions()

        class _Fake:
            chat = _Chat()

        monkeypatch.setattr(client, "client", _Fake())
        got = run(_collect(client.stream_chat([Message(role=Role.USER, content="q")])))
        assert got == chunks


async def _collect(agen):
    return [c async for c in agen]


# ── Anthropic fake ─────────────────────────────────────────────

def _anthropic_response(blocks, stop="end_turn", usage=None):
    class _Usage:
        pass

    resp = type("Resp", (), {})()
    resp.content = blocks
    resp.stop_reason = stop
    if usage:
        u = _Usage()
        u.input_tokens = usage[0]
        u.output_tokens = usage[1]
        u.cache_read_input_tokens = usage[2] if len(usage) > 2 else 0
        u.cache_creation_input_tokens = usage[3] if len(usage) > 3 else 0
        resp.usage = u
    else:
        resp.usage = None
    return resp


def _tool_use_block(block_id, name, args):
    return type("B", (), {"type": "tool_use", "id": block_id, "name": name, "input": args})()


def _text_block(text):
    return type("B", (), {"type": "text", "text": text})()


def _make_anthropic(monkeypatch, resp):
    client = AnthropicClient(model="test", api_key="k")
    recorded = {}

    class _Messages:
        async def create(self, **kwargs):
            recorded["kwargs"] = kwargs
            return resp

        def stream(self, **kwargs):
            recorded["stream_kwargs"] = kwargs
            return _FakeStream()

    class _Fake:
        messages = _Messages()

    monkeypatch.setattr(client, "client", _Fake())
    return client, recorded


class _FakeStream:
    class _TextStream:
        def __init__(self):
            self._texts = ["a", "b", "c"]

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._texts:
                raise StopAsyncIteration
            return self._texts.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def text_stream(self):
        return self._TextStream()


class TestAnthropicClient:

    def test_system_separated(self, monkeypatch):
        client, recorded = _make_anthropic(monkeypatch, _anthropic_response([_text_block("ok")]))
        run(client.chat([
            Message(role=Role.SYSTEM, content="系统提示"),
            Message(role=Role.USER, content="你好"),
        ]))
        kwargs = recorded["kwargs"]
        # cache 启用时 system 是 [{text, cache_control}] 结构；取文本断言
        system_text = kwargs["system"][0]["text"] if isinstance(kwargs["system"], list) else kwargs["system"]
        assert system_text.strip() == "系统提示"
        # 消息里不含 system（已分离）
        assert all(m["role"] != "system" for m in kwargs["messages"])

    def test_tool_result_block(self, monkeypatch):
        """TOOL 消息 → tool_result 块（带 tool_use_id）"""
        client, recorded = _make_anthropic(monkeypatch, _anthropic_response([_text_block("ok")]))
        run(client.chat([Message(role=Role.TOOL, content="结果", tool_call_id="tu_1")]))
        content = recorded["kwargs"]["messages"][0]["content"]
        assert content[0]["type"] == "tool_result"
        assert content[0]["tool_use_id"] == "tu_1"

    def test_assistant_tool_use_blocks(self, monkeypatch):
        """assistant 带 tool_calls → tool_use 块（text + tool_use 混合）"""
        client, recorded = _make_anthropic(monkeypatch, _anthropic_response([_text_block("ok")]))
        run(client.chat([Message(
            role=Role.ASSISTANT, content="先做这个",
            tool_calls=[{"id": "t1", "function": {"name": "f", "arguments": '{"a":1}'}}],
        )]))
        content = recorded["kwargs"]["messages"][0]["content"]
        types = [b["type"] for b in content]
        assert types == ["text", "tool_use"]
        assert content[1]["id"] == "t1"
        assert content[1]["name"] == "f"

    def test_response_parsed_tool_use_and_text(self, monkeypatch):
        client, recorded = _make_anthropic(monkeypatch, _anthropic_response([
            _text_block("第一段"),
            _tool_use_block("tu1", "search", {"q": "x"}),
            _text_block("第二段"),
        ]))
        resp = run(client.chat([Message(role=Role.USER, content="hi")]))
        assert resp.content == "第一段\n第二段"
        assert resp.tool_calls == [ToolCall(id="tu1", name="search", arguments={"q": "x"})]

    def test_cache_control_applied(self, monkeypatch):
        """Prompt Caching: system 带 cache_control；消息内容转 text 块"""
        client, recorded = _make_anthropic(monkeypatch, _anthropic_response([_text_block("ok")]))
        run(client.chat([
            Message(role=Role.SYSTEM, content="可缓存系统提示"),
            Message(role=Role.USER, content="普通消息"),
        ]))
        kwargs = recorded["kwargs"]
        assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
        # 消息内容被转成带 cache_control 的 text 块
        assert kwargs["messages"][0]["content"][0]["type"] == "text"
        assert kwargs["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_cache_disabled_plain_system(self, monkeypatch):
        client, recorded = _make_anthropic(monkeypatch, _anthropic_response([_text_block("ok")]))
        client.with_cache(False)
        run(client.chat([Message(role=Role.SYSTEM, content="系统提示")]))
        assert recorded["kwargs"]["system"] == "系统提示"

    def test_usage_with_cache_tokens(self, monkeypatch):
        client, recorded = _make_anthropic(monkeypatch, _anthropic_response(
            [_text_block("ok")], usage=(10, 20, 5, 3),
        ))
        resp = run(client.chat([Message(role=Role.USER, content="q")]))
        assert resp.usage["input_tokens"] == 10
        assert resp.usage["cache_read_tokens"] == 5
        assert resp.usage["cache_creation_tokens"] == 3

    def test_anthropic_tools_converted(self, monkeypatch):
        """Anthropic 工具定义 → {name, description, input_schema}"""
        client, recorded = _make_anthropic(monkeypatch, _anthropic_response([_text_block("ok")]))
        run(client.chat(
            [Message(role=Role.USER, content="hi")],
            tools=[ToolDefinition(name="search", description="搜", parameters={"type": "object"})],
        ))
        assert recorded["kwargs"]["tools"] == [{
            "name": "search", "description": "搜", "input_schema": {"type": "object"},
        }]

    def test_anthropic_stream(self, monkeypatch):
        client, recorded = _make_anthropic(monkeypatch, _anthropic_response([_text_block("ok")]))
        got = run(_collect(client.stream_chat([
            Message(role=Role.SYSTEM, content="sys"),
            Message(role=Role.USER, content="q"),
        ])))
        assert got == ["a", "b", "c"]


class TestImportErrors:

    def test_openai_missing_sdk(self, monkeypatch):
        """openai SDK 缺失 → 构造时抛 ImportError（懒加载设计）"""
        import sys

        monkeypatch.setitem(sys.modules, "openai", None)
        with pytest.raises(ImportError):
            OpenAIClient(model="t", api_key="k")

    def test_anthropic_missing_sdk(self, monkeypatch):
        """anthropic SDK 缺失 → 构造时抛 ImportError（懒加载设计）"""
        import sys

        monkeypatch.setitem(sys.modules, "anthropic", None)
        with pytest.raises(ImportError):
            AnthropicClient(model="t", api_key="k")


class TestStreamRetryEdge:

    def test_first_chunk_non_retryable_raises(self):
        """流式首块失败且不可重试 → 直接抛（不静默）"""
        from core.llm import LLMClient

        class Boom(LLMClient):
            async def chat(self, messages, tools=None, temperature=0.7,
                           max_tokens=4096, stream=False):
                raise ValueError("参数错误（不可重试）")

        llm = Boom("t")
        with pytest.raises(ValueError):
            run(_collect(llm.stream_chat_with_retry(
                [Message(role=Role.USER, content="q")], max_retries=2,
            )))

    def test_first_chunk_empty_stream_counts_usage(self):
        """空流（无内容）也算一次成功调用并计指标"""
        from core.llm import LLMClient, LLMResponse

        class Empty(LLMClient):
            async def chat(self, messages, tools=None, temperature=0.7,
                           max_tokens=4096, stream=False):
                return LLMResponse(content="")  # 基类兜底，实际走 stream_chat

            async def stream_chat(self, messages, temperature=0.7, max_tokens=4096):
                if False:  # pragma: no cover — 空异步生成器
                    yield ""

        llm = Empty("t")
        got = run(_collect(llm.stream_chat_with_retry(
            [Message(role=Role.USER, content="q")], max_retries=2,
        )))
        assert got == []
        assert llm.usage_stats["call_count"] == 1
