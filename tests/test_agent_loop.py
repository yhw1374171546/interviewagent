"""
ReAct Agent 主循环 + 多 Agent 编排测试（阶段 3）
================================================
覆盖 Agent.run 的思考/工具调用/终止，Orchestrator 的串行/并行/辩论。
全部离线（FakeLLM + 假工具），CI 可直接运行。
"""

import asyncio

import pytest

from core.agent import Agent, AgentConfig, RunResult
from core.llm import LLMClient, LLMResponse, ToolCall
from core.orchestrator import OrchestrationMode, Orchestrator
from tools.base import tool


def run(coro):
    return asyncio.run(coro)


class _AgentLLM(LLMClient):
    """按顺序返回预设响应（可含 tool_calls）"""

    def __init__(self, responses):
        super().__init__(model="fake-agent")
        self.responses = list(responses)
        self.calls = 0

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096, stream=False):
        idx = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[idx]


@tool(name="echo", description="回显输入")
async def echo(text: str) -> str:
    return f"echo:{text}"


@tool(name="boom", description="会抛异常的工具")
async def boom() -> str:
    raise RuntimeError("工具内部错误")


# ── Agent.run ───────────────────────────────────────────────────

class TestAgentRun:

    def test_no_tool_calls_returns_answer(self):
        llm = _AgentLLM([LLMResponse(content="最终答案")])
        agent = Agent(llm, config=AgentConfig(verbose=False))
        result = run(agent.run("问题"))
        assert result.answer == "最终答案"
        assert result.tool_calls_count == 0
        assert result.steps[-1].state.value == "finished"

    def test_with_tool_call_executes_then_answers(self):
        responses = [
            LLMResponse(content="", tool_calls=[ToolCall(id="1", name="echo", arguments={"text": "hi"})]),
            LLMResponse(content="完成"),
        ]
        agent = Agent(_AgentLLM(responses), tools=[echo], config=AgentConfig(verbose=False))
        result = run(agent.run("用 echo 工具"))
        assert result.answer == "完成"
        assert result.tool_calls_count == 1
        assert "echo:hi" in result.steps[0].observation

    def test_max_steps_forces_summary(self):
        resp = LLMResponse(content="", tool_calls=[ToolCall(id="1", name="echo", arguments={"text": "x"})])
        agent = Agent(_AgentLLM([resp] * 10), tools=[echo], config=AgentConfig(max_steps=2, verbose=False))
        result = run(agent.run("无限循环"))
        assert isinstance(result, RunResult)
        assert result.tool_calls_count == 2

    def test_execute_tool_error_returns_error_string(self):
        agent = Agent(_AgentLLM([]), tools=[boom], config=AgentConfig(verbose=False))
        result = run(agent._execute_tool(ToolCall(id="1", name="boom", arguments={})))
        assert result.startswith("错误:")

    def test_history_and_token_usage(self):
        llm = _AgentLLM([LLMResponse(content="答案", usage={"prompt_tokens": 10, "completion_tokens": 5})])
        agent = Agent(llm, config=AgentConfig(verbose=False))
        run(agent.run("问题"))
        assert agent.token_usage == 15
        assert len(agent.history) >= 2

    def test_run_stream_yields_steps(self):
        llm = _AgentLLM([LLMResponse(content="流式答案")])
        agent = Agent(llm, config=AgentConfig(verbose=False))
        steps = run(_collect_stream(agent, "问题"))
        assert len(steps) >= 1
        assert steps[0].state.value == "finished"


async def _collect_stream(agent, user_input):
    steps = []
    async for step in agent.run_stream(user_input):
        steps.append(step)
    return steps


# ── Orchestrator ────────────────────────────────────────────────

class _FakeAgent:
    """鸭子类型 Agent — Orchestrator 只依赖 run() → RunResult"""

    def __init__(self, answer: str, tokens: int = 10):
        self._answer = answer
        self._tokens = tokens

    async def run(self, task: str) -> RunResult:
        return RunResult(answer=self._answer, total_tokens=self._tokens)


class TestOrchestrator:

    def test_register_unregister_names(self):
        orch = Orchestrator()
        orch.register("a", _FakeAgent("A"))
        assert orch.agent_names == ["a"]
        orch.unregister("a")
        assert orch.agent_names == []

    def test_run_sequential(self):
        orch = Orchestrator()
        orch.register("a", _FakeAgent("A"))
        orch.register("b", _FakeAgent("B"))
        result = run(orch.run_sequential("task", ["a", "b"]))
        assert result.final_answer == "B"  # 最后一个 Agent 的输出
        assert result.mode == OrchestrationMode.SEQUENTIAL
        assert result.total_tokens == 20

    def test_run_sequential_unregistered_raises(self):
        orch = Orchestrator()
        with pytest.raises(ValueError):
            run(orch.run_sequential("task", ["missing"]))

    def test_run_parallel(self):
        orch = Orchestrator()
        orch.register("a", _FakeAgent("A"))
        orch.register("b", _FakeAgent("B"))
        result = run(orch.run_parallel("task", ["a", "b"]))
        assert result.mode == OrchestrationMode.PARALLEL
        assert result.final_answer  # 汇总 Agent 的输出

    def test_run_debate(self):
        orch = Orchestrator()
        orch.register("d1", _FakeAgent("论点1"))
        orch.register("d2", _FakeAgent("论点2"))
        orch.register("judge", _FakeAgent("最终裁决"))
        result = run(orch.run_debate("topic", ["d1", "d2"], "judge", rounds=1))
        assert result.mode == OrchestrationMode.DEBATE
        assert result.final_answer == "最终裁决"

    def test_run_debate_missing_judge_raises(self):
        orch = Orchestrator()
        with pytest.raises(ValueError):
            run(orch.run_debate("topic", [], "no_judge"))
