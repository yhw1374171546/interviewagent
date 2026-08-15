"""
工具调用评测框架测试
====================
覆盖 eval/tool_use_eval.py 的判定逻辑:
    - 工具选择正确率判定（_tool_success）
    - 答案关键字匹配（_has_keyword）
    - 工具序列提取（_tools_used）
    - 任务集完整性（期望工具/参数/关键字配对）

全离线（纯函数 + Stub LLM 跑通框架），CI 可直接运行。
"""

import asyncio
import json

from eval.tool_use_eval import (
    ALL_TOOLS,
    TASKS,
    _build_stub_sequences,
    _has_keyword,
    _StubLLM,
    _tool_success,
    _tools_used,
    run_task,
)


def run(coro):
    return asyncio.run(coro)


class TestToolSuccess:

    def test_exact_match(self):
        assert _tool_success(["calculator"], ["calculator"]) is True

    def test_expected_subset_of_used(self):
        """实际用了期望的全部工具即算正确（允许多余）"""
        assert _tool_success(["a", "b"], ["a"]) is True
        assert _tool_success(["a", "b", "c"], ["a", "c"]) is True

    def test_missing_expected_fails(self):
        assert _tool_success(["a"], ["a", "b"]) is False
        assert _tool_success([], ["a"]) is False

    def test_no_tool_expected(self):
        assert _tool_success([], []) is True
        assert _tool_success(["a"], []) is False  # 不该用工具却用了


class TestHasKeyword:

    def test_all_keywords_present(self):
        assert _has_keyword("结果是 36 元", ["36"]) is True
        assert _has_keyword("上海 22°C 小雨", ["22"]) is True

    def test_missing_keyword_fails(self):
        assert _has_keyword("结果是 40", ["36"]) is False

    def test_empty_keywords_always_pass(self):
        assert _has_keyword("随便什么", []) is True


class TestToolsUsed:

    def test_extract_from_steps(self):
        from core.agent import AgentState, AgentStep

        steps = [
            AgentStep(step_num=1, action=json.dumps(
                [{"name": "a", "args": {}}], ensure_ascii=False
            ), state=AgentState.ACTING),
            AgentStep(step_num=2, action=json.dumps(
                [{"name": "b", "args": {}}, {"name": "c", "args": {}}], ensure_ascii=False
            ), state=AgentState.ACTING),
            AgentStep(step_num=3, action="", state=AgentState.FINISHED),
        ]
        assert _tools_used(type("R", (), {"steps": steps})()) == ["a", "b", "c"]

    def test_empty_steps(self):
        assert _tools_used(type("R", (), {"steps": []})()) == []


class TestTaskSet:

    def test_expected_tools_args_keywords_align(self):
        """任务集完整性: 期望工具数与期望参数数一致"""
        for t in TASKS:
            assert len(t["expected_tools"]) == len(t["expected_args"]), t["name"]

    def test_expected_tools_registered(self):
        """任务集引用的工具必须已注册"""
        registered = {t.__name__ for t in ALL_TOOLS}
        for t in TASKS:
            for tool_name in t["expected_tools"]:
                assert tool_name in registered, f"{t['name']} 引用了未注册工具 {tool_name}"

    def test_stub_sequences_match_tasks(self):
        seqs = _build_stub_sequences()
        assert set(seqs.keys()) == {t["name"] for t in TASKS}


class TestFrameworkRuns:

    def test_mock_framework_runs_all_tasks(self):
        """Stub 预设正确序列 → 框架能跑完所有任务且工具选择全对"""
        llm = _StubLLM(_build_stub_sequences())
        results = [run(run_task(llm, t, None)) for t in TASKS]
        # 工具选择必须全对（mock 预设的就是正确序列）
        assert all(r["tool_correct"] for r in results)
        # 每个任务都执行了期望次数的工具调用
        for r, t in zip(results, TASKS):
            assert r["tool_calls"] == len(t["expected_tools"]), t["name"]
        # 无工具任务: 不应调用任何工具
        no_tool = next(r for r in results if r["name"] == "无需工具-直接回答")
        assert no_tool["tool_calls"] == 0

    def test_mock_task_success_single_tool(self):
        """单工具任务在 mock 下答案应含工具结果"""
        llm = _StubLLM(_build_stub_sequences())
        calc = next(t for t in TASKS if t["name"] == "单工具-计算")
        r = run(run_task(llm, calc, None))
        assert r["answer_correct"] is True
