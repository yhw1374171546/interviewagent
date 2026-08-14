"""
代码裁判测试（阶段 3）
====================
覆盖 AST 安全审计、沙箱执行、输出比对（真 bug 回归）、超时 kill。
全部离线（subprocess 执行本地 Python），CI 可直接运行。
"""

import asyncio

from interview.code_judge import (
    PRESET_CODE_QUESTIONS,
    CodeQuestion,
    audit_code_safety,
    format_judge_report,
    run_judge,
)
from interview.code_judge import (
    TestCase as CodeTestCase,
)

COD_LRU = PRESET_CODE_QUESTIONS[0]


def run(coro):
    return asyncio.run(coro)


# ── 正确的 LRU 实现（OrderedDict） ──────────────────────────────

CORRECT_LRU = '''from collections import OrderedDict


class LRUCache:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.cache = OrderedDict()

    def get(self, key: int) -> int:
        if key not in self.cache:
            return -1
        self.cache.move_to_end(key)
        return self.cache[key]

    def put(self, key: int, value: int) -> None:
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)
'''

# ── 有 Bug 的实现：get 不维护 LRU 顺序 ──────────────────────────

BUGGY_LRU = '''class LRUCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.data = {}
        self.order = []

    def get(self, key):
        return self.data.get(key, -1)

    def put(self, key, value):
        if key not in self.data:
            if len(self.data) >= self.capacity:
                old = self.order.pop(0)
                del self.data[old]
            self.order.append(key)
        self.data[key] = value
'''


# ── AST 安全审计 ───────────────────────────────────────────────

class TestAuditCodeSafety:

    def test_safe_code_passes(self):
        safe, err = audit_code_safety("x = 1\nprint(x)")
        assert safe is True
        assert err == ""

    def test_syntax_error_rejected(self):
        safe, err = audit_code_safety("def f(:")
        assert safe is False
        assert "语法错误" in err

    def test_forbidden_call_rejected(self):
        safe, err = audit_code_safety("eval('1+1')")
        assert safe is False
        assert "eval" in err

    def test_os_system_rejected(self):
        safe, err = audit_code_safety("import os\nos.system('ls')")
        assert safe is False
        assert "os.system" in err

    def test_unallowed_import_rejected(self):
        safe, err = audit_code_safety("import requests")
        assert safe is False
        assert "requests" in err

    def test_allowed_stdlib_import_passes(self):
        safe, err = audit_code_safety("import math\nprint(math.sqrt(4))")
        assert safe is True

    def test_unallowed_ast_node_rejected(self):
        safe, err = audit_code_safety("global x")
        assert safe is False
        assert "Global" in err


# ── 沙箱执行 + 输出比对 ────────────────────────────────────────

class TestRunJudge:

    def test_correct_lru_passes_all(self):
        result = run(run_judge(CORRECT_LRU, COD_LRU))
        assert result.passed is True
        assert result.passed_tests == result.total_tests == 5

    def test_buggy_lru_fails_output_comparison(self):
        """回归测试: get 不维护 LRU 顺序的实现必须在「get后淘汰最久未使用」失败"""
        result = run(run_judge(BUGGY_LRU, COD_LRU))
        assert result.passed is False
        assert result.passed_tests == 4
        failed_names = [d["name"] for d in result.details if not d["passed"]]
        assert "get后淘汰最久未使用" in failed_names

    def test_malicious_code_blocked_before_execution(self):
        result = run(run_judge("import os\nos.system('rm -rf /')", COD_LRU))
        assert result.passed is False
        assert result.details[0]["name"] == "安全检查"

    def test_timeout_kills_runaway_loop(self):
        runaway = "while True:\n    pass"
        result = run(run_judge(runaway, COD_LRU, timeout_sec=0.2))
        assert result.passed is False
        assert result.details[0]["name"] == "超时"

    def test_format_report_contains_verdict(self):
        result = run(run_judge(CORRECT_LRU, COD_LRU))
        report = format_judge_report(result)
        assert "全部通过" in report


# ── C++ 判题（多语言） ─────────────────────────────────────────

CPP_TWOSUM = CodeQuestion(
    id="CPP001", title="两数之和", description="", function_signature="vector<int> two_sum(vector<int>&, int)",
    example_input="", example_output="",
    test_cases=[
        CodeTestCase(name="基础", input_code='vector<int> nums = {2, 7, 11, 15};\nvector<int> r = two_sum(nums, 9);\ncout << r[0] << " " << r[1];', expected="0 1"),
        CodeTestCase(name="重复元素", input_code='vector<int> nums = {3, 3};\nvector<int> r = two_sum(nums, 6);\ncout << r[0] << " " << r[1];', expected="0 1"),
    ],
)

CPP_CORRECT = "vector<int> two_sum(vector<int>& nums, int target) {\n    for (int i = 0; i < nums.size(); i++)\n        for (int j = i + 1; j < nums.size(); j++)\n            if (nums[i] + nums[j] == target) return {i, j};\n    return {};\n}"


class TestCppJudge:

    def test_cpp_correct_passes(self):
        result = run(run_judge(CPP_CORRECT, CPP_TWOSUM, language="cpp"))
        assert result.passed is True
        assert result.passed_tests == 2

    def test_cpp_wrong_fails_with_expected_got(self):
        wrong = "vector<int> two_sum(vector<int>& nums, int target) { return {0, 0}; }"
        result = run(run_judge(wrong, CPP_TWOSUM, language="cpp"))
        assert result.passed is False
        # 修复后 expected/got 应正确解析（不含 marker 前缀）
        assert result.details[0]["expected"] == "0 1"

    def test_cpp_compile_error(self):
        bad = "vector<int> two_sum(vector<int>& nums, int target) { return {0, ; }"
        result = run(run_judge(bad, CPP_TWOSUM, language="cpp"))
        assert result.passed is False
        assert result.details[0]["name"] == "编译错误"

    def test_cpp_forbidden_system_blocked(self):
        result = run(run_judge("int main(){ system(\"ls\"); }", CPP_TWOSUM, language="cpp"))
        assert result.passed is False
        assert result.details[0]["name"] == "安全检查"

    def test_unsupported_language(self):
        result = run(run_judge(CPP_CORRECT, CPP_TWOSUM, language="ruby"))
        assert result.passed is False
        assert result.details[0]["name"] == "不支持的语言"
