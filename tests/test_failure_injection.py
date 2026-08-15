"""
失败注入评测框架测试
====================
覆盖 eval/failure_injection_eval.py 的:
    - FlakyTool 故障注入行为（fail_times/always_fail/timeout/bad_data）
    - 场景定义完整性（工具注册/期望/关键字配对）
    - Agent 集成: 工具异常不崩溃、持续失败触发循环检测

全离线，CI 可直接运行。
"""

import asyncio

import pytest

from core.agent import Agent, AgentConfig
from core.llm import LLMClient, LLMResponse, ToolCall
from eval.failure_injection_eval import (
    SCENARIOS,
    FlakyTool,
    _build_stub_sequences,
    get_city_weather,
)


def run(coro):
    return asyncio.run(coro)


# ── FlakyTool 行为 ─────────────────────────────────────────────

class TestFlakyTool:

    def test_fail_times_recovers(self):
        """fail_times=1: 第一次抛异常，之后正常"""
        ft = FlakyTool(get_city_weather, fail_times=1)
        with pytest.raises(RuntimeError):
            run(ft(city="北京"))
        result = run(ft(city="北京"))
        assert "晴" in str(result)

    def test_always_fail(self):
        ft = FlakyTool(get_city_weather, always_fail=True)
        with pytest.raises(RuntimeError):
            run(ft(city="北京"))
        with pytest.raises(RuntimeError):
            run(ft(city="上海"))

    def test_bad_data(self):
        ft = FlakyTool(get_city_weather, bad_data="<html>500</html>")
        assert run(ft(city="北京")) == "<html>500</html>"

    def test_timeout_simulated(self):
        import time

        ft = FlakyTool(get_city_weather, timeout_sec=0.2)
        t0 = time.time()
        run(ft(city="北京"))
        assert time.time() - t0 >= 0.15, "超时模拟应至少等待指定时长"

    def test_meta_preserved_from_wrapped(self):
        """FlakyTool 复用原工具 schema（可被 ToolRegistry 注册）"""
        from tools.base import ToolRegistry

        registry = ToolRegistry()
        registry.register(FlakyTool(get_city_weather, fail_times=1))
        assert "get_city_weather" in registry.tool_names()


# ── 场景定义完整性 ─────────────────────────────────────────────

class TestScenarios:

    def test_each_scenario_has_tools(self):
        for s in SCENARIOS:
            assert len(s["tools"]) >= 1, s["name"]

    def test_scenario_prompts_unique(self):
        prompts = [s["prompt"] for s in SCENARIOS]
        assert len(prompts) == len(set(prompts))

    def test_stub_sequences_cover_all_scenarios(self):
        seqs = _build_stub_sequences()
        assert set(seqs.keys()) == {s["name"] for s in SCENARIOS}


# ── Agent 集成: 异常不崩溃 + 循环兜底 ──────────────────────────

class _LoopLLM(LLMClient):
    """永远返回同一个工具调用（模拟 LLM 在持续失败下仍重试）"""

    def __init__(self, tc: ToolCall):
        super().__init__(model="loop")
        self.tc = tc

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096, stream=False):
        return LLMResponse(
            content="", tool_calls=[self.tc],
            usage={"prompt_tokens": 5, "completion_tokens": 5},
        )


class TestAgentFailureHandling:

    def test_tool_exception_does_not_crash_agent(self):
        """工具抛异常 → Agent 不崩溃，把错误作为观察返回"""

        ft = FlakyTool(get_city_weather, always_fail=True)
        llm = _LoopLLM(ToolCall(id="1", name="get_city_weather", arguments={"city": "北京"}))
        agent = Agent(llm, tools=[ft], config=AgentConfig(verbose=False, max_tool_calls=5))
        result = run(agent.run("查询天气"))
        # 不崩溃 + 最终给出答案（循环检测终止）
        assert result.answer
        assert "工具调用循环" in result.answer or "错误" in result.answer

    def test_fail_times_then_success_completes(self):
        """瞬时失败后恢复 → Agent 最终完成（LLM 重试）"""

        ft = FlakyTool(get_city_weather, fail_times=1)

        class _RetryLLM(LLMClient):
            def __init__(self):
                super().__init__(model="retry")
                self.calls = 0

            async def chat(self, messages, tools=None, temperature=0.7,
                           max_tokens=4096, stream=False):
                self.calls += 1
                # 第一次调工具，第二次看到错误后重试，第三次回答
                if self.calls <= 2:
                    return LLMResponse(
                        content="", tool_calls=[ToolCall(
                            id=f"t{self.calls}", name="get_city_weather",
                            arguments={"city": "北京"},
                        )],
                        usage={"prompt_tokens": 5, "completion_tokens": 5},
                    )
                return LLMResponse(
                    content="北京今天晴，25°C",
                    usage={"prompt_tokens": 5, "completion_tokens": 5},
                )

        agent = Agent(_RetryLLM(), tools=[ft], config=AgentConfig(verbose=False))
        result = run(agent.run("查询北京天气"))
        assert result.tool_calls_count == 2  # 失败 1 次 + 成功 1 次
        assert "25" in result.answer
