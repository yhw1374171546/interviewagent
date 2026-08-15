"""
循环检测测试
============
覆盖 core/loop_detector.py 的检测信号（同参数重复/同工具重复/观察重复）
与 Agent 集成（循环序列提前终止、正常序列不误判）。

全离线（确定性规则 + FakeLLM），CI 可直接运行。
"""

import asyncio

from core.agent import Agent, AgentConfig
from core.llm import LLMClient, LLMResponse, ToolCall
from core.loop_detector import LoopDetector
from tools.base import tool


def run(coro):
    return asyncio.run(coro)


# ── 检测器单测 ─────────────────────────────────────────────────

class TestLoopDetector:

    def test_same_args_repeats_detected(self):
        """同一工具+相同参数重复 2 次 → 检测"""
        d = LoopDetector()
        assert d.record("search", {"q": "x"})["loop_detected"] is False
        r = d.record("search", {"q": "x"})
        assert r["loop_detected"] is True
        assert "相同参数" in r["reason"]

    def test_same_tool_different_args_detected(self):
        """同一工具不同参数重复 3 次 → 检测（无进展）"""
        d = LoopDetector()
        assert d.record("search", {"q": "a"})["loop_detected"] is False
        assert d.record("search", {"q": "b"})["loop_detected"] is False
        r = d.record("search", {"q": "c"})
        assert r["loop_detected"] is True
        assert "连续调用" in r["reason"]

    def test_observation_repeat_detected(self):
        """工具返回相同结果连续 3 次 → 检测（无新信息）"""
        d = LoopDetector()
        assert d.record("search", {"q": "a"}, observation="结果A")["loop_detected"] is False
        assert d.record("search", {"q": "b"}, observation="结果A")["loop_detected"] is False
        r = d.record("search", {"q": "c"}, observation="结果A")
        assert r["loop_detected"] is True
        assert "相同结果" in r["reason"]

    def test_normal_sequence_not_detected(self):
        """正常多工具序列不误判"""
        d = LoopDetector()
        seq = [
            ("search", {"q": "a"}, "结果1"),
            ("calculator", {"expression": "1+1"}, "2"),
            ("search", {"q": "b"}, "结果2"),
        ]
        for tool_name, args, obs in seq:
            assert d.record(tool_name, args, observation=obs)["loop_detected"] is False

    def test_window_prevents_history_accumulation(self):
        """窗口滑动: 间隔多次正常调用后，旧重复不计入"""
        d = LoopDetector(window=3)
        d.record("search", {"q": "a"})
        d.record("search", {"q": "b"})
        # 窗口满 3 后，a 被挤出，不再触发
        r = d.record("calc", {"e": "1"})
        assert r["loop_detected"] is False

    def test_reset(self):
        d = LoopDetector()
        d.record("search", {"q": "x"})
        d.record("search", {"q": "x"})
        d.reset()
        assert d.record("search", {"q": "x"})["loop_detected"] is False


# ── Agent 集成 ─────────────────────────────────────────────────

class _LoopLLM(LLMClient):
    """预设响应: 永远返回同一个工具调用（模拟 LLM 陷入循环）"""

    def __init__(self, tool_call: ToolCall):
        super().__init__(model="loop-fake")
        self.tool_call = tool_call

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096, stream=False):
        return LLMResponse(
            content="", tool_calls=[self.tool_call],
            usage={"prompt_tokens": 5, "completion_tokens": 5},
        )


class _NormalLLM(LLMClient):
    """预设响应: 先调工具再正常回答（不循环）"""

    def __init__(self, tool_call: ToolCall):
        super().__init__(model="normal-fake")
        self.tool_call = tool_call
        self.calls = 0

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096, stream=False):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content="", tool_calls=[self.tool_call],
                usage={"prompt_tokens": 5, "completion_tokens": 5},
            )
        return LLMResponse(
            content="完成", usage={"prompt_tokens": 5, "completion_tokens": 5},
        )


@tool(name="search", description="搜索")
async def search(q: str) -> str:
    return f"关于 {q} 的结果"


class TestAgentLoopDetection:

    def test_loop_terminated_early(self):
        """LLM 反复调同一工具 → 循环检测提前终止（而非跑满 max_steps）"""
        llm = _LoopLLM(ToolCall(id="1", name="search", arguments={"q": "x"}))
        agent = Agent(
            llm, tools=[search],
            config=AgentConfig(verbose=False, max_steps=10, max_tool_calls=20),
        )
        result = run(agent.run("搜索 x"))
        # 同工具+同参数 2 次即触发（loop_same_args_repeats=2）
        assert result.tool_calls_count <= 3, f"应在循环早期终止，实际 {result.tool_calls_count} 次"
        assert "循环" in result.answer
        assert "搜索" in result.answer or "search" in result.answer

    def test_loop_detection_disabled(self):
        """关闭循环检测 → 跑满 max_tool_calls（旧行为）"""
        llm = _LoopLLM(ToolCall(id="1", name="search", arguments={"q": "x"}))
        agent = Agent(
            llm, tools=[search],
            config=AgentConfig(
                verbose=False, max_steps=10, max_tool_calls=4, loop_detection=False,
            ),
        )
        result = run(agent.run("搜索 x"))
        assert result.tool_calls_count == 4  # 跑满上限

    def test_normal_sequence_not_interrupted(self):
        """正常「工具→回答」序列不受循环检测影响"""
        llm = _NormalLLM(ToolCall(id="1", name="search", arguments={"q": "hello"}))
        agent = Agent(
            llm, tools=[search],
            config=AgentConfig(verbose=False, max_steps=10),
        )
        result = run(agent.run("搜索 hello"))
        assert result.tool_calls_count == 1
        assert "循环" not in result.answer
        assert result.answer == "完成"
