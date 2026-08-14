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

    def test_is_not_comparison_allowed(self):
        """链表/树题的 `is not None` 必须放行"""
        safe, err = audit_code_safety("def f(x):\n    return x is not None")
        assert safe is True, err

    def test_list_comprehension_allowed(self):
        """列表推导（含 comprehension 子节点）必须放行"""
        safe, err = audit_code_safety("def f(xs):\n    return [x * 2 for x in xs]")
        assert safe is True, err

    def test_nonlocal_allowed(self):
        """闭包 nonlocal 声明必须放行"""
        code = "def outer():\n    total = 0\n    def inner():\n        nonlocal total\n        total += 1\n    return inner"
        safe, err = audit_code_safety(code)
        assert safe is True, err


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

    def test_no_test_cases_returns_se_not_ac(self):
        """回归: 无 test_cases 时必须 SE，禁止 0/0 判 AC 假阳性"""
        q = CodeQuestion(
            id="NOCASE", title="", description="", function_signature="class MinStack:",
            example_input="", example_output="", test_cases=[],
        )
        result = run(run_judge("print(1)", q))
        assert result.passed is False
        assert result.verdict == "SE"
        assert result.details[0]["name"] == "无测试用例"

    def test_format_report_contains_verdict(self):
        result = run(run_judge(CORRECT_LRU, COD_LRU))
        report = format_judge_report(result)
        assert "全部通过" in report


# ── 链表/树判题（节点工具注入） ────────────────────────────────

LC_ADD_TWO = CodeQuestion(
    id="LC002T", title="Add Two Numbers", description="", function_signature="",
    example_input="", example_output="",
    test_cases=[
        CodeTestCase(
            name="进位",
            input_code=(
                "sol = Solution()\n"
                "print(linkedlist_to_list(sol.addTwoNumbers("
                "l1=list_to_linkedlist([2, 4, 3]), l2=list_to_linkedlist([5, 6, 4]))))"
            ),
            expected="[7, 0, 8]",
        ),
    ],
)

LC_ADD_CORRECT = '''class Solution:
    def addTwoNumbers(self, l1: Optional[ListNode], l2: Optional[ListNode]) -> Optional[ListNode]:
        dummy = ListNode(0)
        cur = dummy
        carry = 0
        while l1 or l2 or carry:
            s = carry
            if l1:
                s += l1.val
                l1 = l1.next
            if l2:
                s += l2.val
                l2 = l2.next
            carry, s = divmod(s, 10)
            cur.next = ListNode(s)
            cur = cur.next
        return dummy.next
'''

LC_TREE_Q = CodeQuestion(
    id="LC094T", title="Inorder Traversal", description="", function_signature="",
    example_input="", example_output="",
    test_cases=[
        CodeTestCase(
            name="中序",
            input_code=(
                "sol = Solution()\n"
                "print(sol.inorderTraversal(list_to_tree([1, None, 2, 3])))"
            ),
            expected="[1, 3, 2]",
        ),
    ],
)

LC_TREE_CORRECT = '''class Solution:
    def inorderTraversal(self, root: Optional[TreeNode]) -> List[int]:
        res = []
        def dfs(node):
            if not node:
                return
            dfs(node.left)
            res.append(node.val)
            dfs(node.right)
        dfs(root)
        return res
'''


class TestNodeJudge:

    def test_linked_list_question_passes(self):
        result = run(run_judge(LC_ADD_CORRECT, LC_ADD_TWO))
        assert result.passed is True
        assert result.passed_tests == 1

    def test_linked_list_wrong_answer_fails(self):
        wrong = "class Solution:\n    def addTwoNumbers(self, l1, l2):\n        return None\n"
        result = run(run_judge(wrong, LC_ADD_TWO))
        assert result.passed is False
        # 区分度: 空链表与期望 [7, 0, 8] 不匹配（不是框架 RE）
        assert result.verdict == "WA"

    def test_tree_question_passes(self):
        result = run(run_judge(LC_TREE_CORRECT, LC_TREE_Q))
        assert result.passed is True

    def test_tree_null_roundtrip(self):
        """层序数组含 null 的构造/序列化往返一致"""
        from interview.code_judge import NODE_UTILS_SOURCE
        ns: dict = {}
        exec(compile(NODE_UTILS_SOURCE, "<utils>", "exec"), ns)
        arr = [3, 9, 20, None, None, 15, 7]
        root = ns["list_to_tree"](arr)
        assert ns["tree_to_list"](root) == arr
        assert ns["linkedlist_to_list"](ns["list_to_linkedlist"]([1, 2, 3])) == [1, 2, 3]


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


# ── ACM 完整程序模式 ──────────────────────────────────────────

ACM_ADD = CodeQuestion(
    id="COD004", title="两数之和", description="", function_signature="（ACM）",
    example_input="", example_output="",
    test_cases=[
        CodeTestCase(name="基础", input_code="1 2", expected="3"),
        CodeTestCase(name="负数", input_code="-1 5", expected="4"),
    ],
)

ACM_CORRECT = "a, b = map(int, input().split())\nprint(a + b)\n"


class TestAcmMode:

    def test_acm_correct_passes(self):
        result = run(run_judge(ACM_CORRECT, ACM_ADD, language="python", mode="acm"))
        assert result.passed is True
        assert result.passed_tests == 2

    def test_acm_wrong_fails(self):
        result = run(run_judge("print(0)\n", ACM_ADD, language="python", mode="acm"))
        assert result.passed is False
        assert result.details[0]["got"] == "0"

    def test_acm_timeout(self):
        result = run(run_judge("while True:\n    pass", ACM_ADD, language="python", mode="acm", timeout_sec=0.2))
        assert result.passed is False
        assert result.details[0]["error"] == "超时"


# ── 判题结论（verdict）分类 ────────────────────────────────────

_PY_Q = CodeQuestion(
    id="P", title="", description="", function_signature="def bpe(c,v)",
    example_input="", example_output="",
    test_cases=[CodeTestCase(name="t1", input_code="print(' '.join(sorted(bpe('ab', 2))))", expected="a b")],
)


class TestVerdict:

    def test_ac_verdict(self):
        r = run(run_judge("def bpe(c, v):\n    return list(set(c))\n", _PY_Q))
        assert r.verdict == "AC"

    def test_wa_verdict(self):
        r = run(run_judge("def bpe(c, v):\n    return ['x']\n", _PY_Q))
        assert r.verdict == "WA"

    def test_re_verdict(self):
        r = run(run_judge("def wrong_name(c, v):\n    return []\n", _PY_Q))
        assert r.verdict == "RE"
        assert "NameError" in r.details[0]["error"]

    def test_tle_verdict(self):
        r = run(run_judge("def bpe(c, v):\n    while True:\n        pass\n", _PY_Q, timeout_sec=0.2))
        assert r.verdict == "TLE"

    def test_ce_verdict(self):
        cpp_q = CodeQuestion(id="C", title="", description="", function_signature="vector<int> f()",
                             example_input="", example_output="",
                             test_cases=[CodeTestCase(name="t1", input_code="cout << 1;", expected="1")])
        r = run(run_judge("vector<int> f() { return {1, ; }", cpp_q, language="cpp"))
        assert r.verdict == "CE"
        assert "error" in r.details[0]["error"]  # 编译器错误信息含行号
