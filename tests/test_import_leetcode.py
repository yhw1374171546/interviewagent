"""
LeetCode 导入器测试
===================
覆盖 extract_signature / gen_test_cases 对链表/树题的用例生成
（节点构造、返回类型包装、原地修改特判）。用 mock 题目，不依赖数据文件。
"""

from tools.import_leetcode import (
    extract_signature,
    gen_test_cases,
    normalize_expected,
)


def _prob(snippet: str, examples: list[dict]) -> dict:
    return {"code_snippets": {"python3": snippet}, "examples": examples}


# ── extract_signature ───────────────────────────────────────────

class TestExtractSignature:

    def test_plain_method(self):
        sig = extract_signature(_prob(
            "class Solution:\n    def twoSum(self, nums: List[int], target: int) -> List[int]:",
            [],
        ))
        assert sig == ("twoSum", [("nums", "List[int]"), ("target", "int")], "List[int]")

    def test_node_method_with_comment_definition(self):
        """注释里的 ListNode 定义不得被误认为方法"""
        sig = extract_signature(_prob(
            "# Definition for singly-linked list.\n"
            "# class ListNode:\n"
            "#     def __init__(self, val=0, next=None):\n"
            "#         self.val = val\n"
            "#         self.next = next\n"
            "class Solution:\n"
            "    def addTwoNumbers(self, l1: Optional[ListNode], l2: Optional[ListNode]) -> Optional[ListNode]:",
            [],
        ))
        assert sig[0] == "addTwoNumbers"
        assert sig[1] == [("l1", "Optional[ListNode]"), ("l2", "Optional[ListNode]")]
        assert sig[2] == "Optional[ListNode]"

    def test_none_return(self):
        sig = extract_signature(_prob(
            "class Solution:\n    def recoverTree(self, root: Optional[TreeNode]) -> None:",
            [],
        ))
        assert sig[2] == "None"


# ── gen_test_cases（节点类型） ──────────────────────────────────

class TestGenNodeCases:

    def test_linked_list_param_wrapped(self):
        cases = gen_test_cases(_prob(
            "class Solution:\n    def addTwoNumbers(self, l1: Optional[ListNode], l2: Optional[ListNode]) -> Optional[ListNode]:",
            [{"example_text": "Input: l1 = [2,4,3], l2 = [5,6,4]\nOutput: [7,0,8]"}],
        ))
        assert len(cases) == 1
        assert "list_to_linkedlist([2,4,3])" in cases[0]["input_code"]
        assert "linkedlist_to_list" in cases[0]["input_code"]
        assert cases[0]["expected"] == "[7, 0, 8]"

    def test_list_of_linkedlist_param(self):
        """mergeKLists: List[Optional[ListNode]] 逐项构造"""
        cases = gen_test_cases(_prob(
            "class Solution:\n    def mergeKLists(self, lists: List[Optional[ListNode]]) -> Optional[ListNode]:",
            [{"example_text": "Input: lists = [[1,4,5],[1,3,4],[2,6]]\nOutput: [1,1,2,3,4,4,5,6]"}],
        ))
        assert len(cases) == 1
        code = cases[0]["input_code"]
        assert "list_to_linkedlist([1, 4, 5])" in code
        assert "list_to_linkedlist([2, 6])" in code

    def test_tree_param_null_converted(self):
        cases = gen_test_cases(_prob(
            "class Solution:\n    def inorderTraversal(self, root: Optional[TreeNode]) -> List[int]:",
            [{"example_text": "Input: root = [1,null,2,3]\nOutput: [1,3,2]"}],
        ))
        assert len(cases) == 1
        code = cases[0]["input_code"]
        assert "list_to_tree([1,None,2,3])" in code
        assert "print(sol.inorderTraversal" in code  # List[int] 不包装

    def test_tree_list_return_wrapped(self):
        """generateTrees: 返回 List[TreeNode] → trees_to_list（修复对象地址 bug）"""
        cases = gen_test_cases(_prob(
            "class Solution:\n    def generateTrees(self, n: int) -> List[Optional[TreeNode]]:",
            [{"example_text": "Input: n = 3\nOutput: [[1,null,2,null,3],[1,null,3,2],[2,1,3],[3,1,null,null,2],[3,2,null,1]]"}],
        ))
        assert len(cases) == 1
        assert "trees_to_list(sol.generateTrees(n=3))" in cases[0]["input_code"]

    def test_in_place_modify(self):
        """recoverTree: 返回 None → 构造节点变量、调用后序列化"""
        cases = gen_test_cases(_prob(
            "class Solution:\n    def recoverTree(self, root: Optional[TreeNode]) -> None:",
            [{"example_text": "Input: root = [1,3,null,null,2]\nOutput: [3,1,null,null,2]"}],
        ))
        assert len(cases) == 1
        code = cases[0]["input_code"]
        assert "_root = list_to_tree([1,3,None,None,2])" in code
        assert "sol.recoverTree(_root)" in code
        assert "print(tree_to_list(_root))" in code
        assert cases[0]["expected"] == "[3, 1, None, None, 2]"

    def test_plain_int_param_unchanged(self):
        cases = gen_test_cases(_prob(
            "class Solution:\n    def twoSum(self, nums: List[int], target: int) -> List[int]:",
            [{"example_text": "Input: nums = [2,7,11,15], target = 9\nOutput: [0,1]"}],
        ))
        assert len(cases) == 1
        assert "sol.twoSum(nums=[2, 7, 11, 15], target=9)" in cases[0]["input_code"]


# ── normalize_expected ──────────────────────────────────────────

class TestNormalizeExpected:

    def test_tree_array_with_null(self):
        assert normalize_expected("[3,1,null,null,2]") == "[3, 1, None, None, 2]"

    def test_bool_output(self):
        assert normalize_expected("true") == "True"

    def test_string_output_strips_quotes(self):
        assert normalize_expected('"abc"') == "abc"
