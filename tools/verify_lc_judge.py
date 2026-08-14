"""
LeetCode 判题用例验证器
======================
对 interview/leetcode_bank.py 里每道带 test_cases 的题：
1. 用正确的参考实现判题 → 必须 AC（用例可被正确解通过）
2. 用一个"总是返回错误答案"的实现判题 → 必须非 AC（区分度）

数据源: 题库 JSON 里自带的 examples 生成的用例。
运行:
    python tools/verify_lc_judge.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from interview.code_judge import CodeQuestion, TestCase, run_judge
from interview.leetcode_bank import LC_QUESTIONS

# ── 参考实现（与 function_signature 匹配的正确答案） ─────────────

SOLUTIONS: dict[str, str] = {
    "LC002": """class Solution:
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
""",
    "LC019": """class Solution:
    def removeNthFromEnd(self, head: Optional[ListNode], n: int) -> Optional[ListNode]:
        dummy = ListNode(0, head)
        fast = slow = dummy
        for _ in range(n):
            fast = fast.next
        while fast.next:
            fast = fast.next
            slow = slow.next
        slow.next = slow.next.next
        return dummy.next
""",
    "LC021": """class Solution:
    def mergeTwoLists(self, list1: Optional[ListNode], list2: Optional[ListNode]) -> Optional[ListNode]:
        dummy = ListNode(0)
        cur = dummy
        while list1 and list2:
            if list1.val <= list2.val:
                cur.next = list1
                list1 = list1.next
            else:
                cur.next = list2
                list2 = list2.next
            cur = cur.next
        cur.next = list1 or list2
        return dummy.next
""",
    "LC023": """class Solution:
    def mergeKLists(self, lists: List[Optional[ListNode]]) -> Optional[ListNode]:
        def merge2(a, b):
            dummy = ListNode(0)
            cur = dummy
            while a and b:
                if a.val <= b.val:
                    cur.next = a
                    a = a.next
                else:
                    cur.next = b
                    b = b.next
                cur = cur.next
            cur.next = a or b
            return dummy.next
        merged = None
        for lst in lists:
            merged = merge2(merged, lst)
        return merged
""",
    "LC024": """class Solution:
    def swapPairs(self, head: Optional[ListNode]) -> Optional[ListNode]:
        dummy = ListNode(0, head)
        prev = dummy
        while prev.next and prev.next.next:
            a = prev.next
            b = a.next
            prev.next = b
            a.next = b.next
            b.next = a
            prev = a
        return dummy.next
""",
    "LC025": """class Solution:
    def reverseKGroup(self, head: Optional[ListNode], k: int) -> Optional[ListNode]:
        dummy = ListNode(0, head)
        group_prev = dummy
        while True:
            kth = group_prev
            for _ in range(k):
                kth = kth.next
                if not kth:
                    return dummy.next
            group_next = kth.next
            prev = group_next
            cur = group_prev.next
            while cur is not group_next:
                nxt = cur.next
                cur.next = prev
                prev = cur
                cur = nxt
            first = group_prev.next
            group_prev.next = prev
            group_prev = first
""",
    "LC094": """class Solution:
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
""",
    "LC095": """class Solution:
    def generateTrees(self, n: int) -> List[Optional[TreeNode]]:
        def gen(lo, hi):
            if lo > hi:
                return [None]
            out = []
            for i in range(lo, hi + 1):
                for l in gen(lo, i - 1):
                    for r in gen(i + 1, hi):
                        out.append(TreeNode(i, l, r))
            return out
        return gen(1, n)
""",
    "LC098": """class Solution:
    def isValidBST(self, root: Optional[TreeNode]) -> bool:
        def check(node, lo, hi):
            if not node:
                return True
            if not (lo < node.val < hi):
                return False
            return check(node.left, lo, node.val) and check(node.right, node.val, hi)
        return check(root, float('-inf'), float('inf'))
""",
    "LC099": """class Solution:
    def recoverTree(self, root: Optional[TreeNode]) -> None:
        vals = []
        def inorder(node):
            if not node:
                return
            inorder(node.left)
            vals.append(node)
            inorder(node.right)
        inorder(root)
        s = sorted(v.val for v in vals)
        for node, v in zip(vals, s):
            node.val = v
""",
    "LC100": """class Solution:
    def isSameTree(self, p: Optional[TreeNode], q: Optional[TreeNode]) -> bool:
        if not p and not q:
            return True
        if not p or not q:
            return False
        return p.val == q.val and self.isSameTree(p.left, q.left) and self.isSameTree(p.right, q.right)
""",
    "LC101": """class Solution:
    def isSymmetric(self, root: Optional[TreeNode]) -> bool:
        def same(a, b):
            if not a and not b:
                return True
            if not a or not b:
                return False
            return a.val == b.val and same(a.left, b.right) and same(a.right, b.left)
        return same(root.left, root.right) if root else True
""",
    "LC102": """class Solution:
    def levelOrder(self, root: Optional[TreeNode]) -> List[List[int]]:
        if not root:
            return []
        res, queue = [], [root]
        while queue:
            level, nxt = [], []
            for node in queue:
                level.append(node.val)
                if node.left:
                    nxt.append(node.left)
                if node.right:
                    nxt.append(node.right)
            res.append(level)
            queue = nxt
        return res
""",
    "LC103": """class Solution:
    def zigzagLevelOrder(self, root: Optional[TreeNode]) -> List[List[int]]:
        if not root:
            return []
        res, queue, left_to_right = [], [root], True
        while queue:
            level = [node.val for node in queue]
            if not left_to_right:
                level.reverse()
            res.append(level)
            nxt = []
            for node in queue:
                if node.left:
                    nxt.append(node.left)
                if node.right:
                    nxt.append(node.right)
            queue = nxt
            left_to_right = not left_to_right
        return res
""",
    "LC113": """class Solution:
    def pathSum(self, root: Optional[TreeNode], targetSum: int) -> List[List[int]]:
        res = []
        def dfs(node, remain, path):
            if not node:
                return
            path = path + [node.val]
            if not node.left and not node.right and remain == node.val:
                res.append(path)
                return
            dfs(node.left, remain - node.val, path)
            dfs(node.right, remain - node.val, path)
        dfs(root, targetSum, [])
        return res
""",
    "LC124": """class Solution:
    def maxPathSum(self, root: Optional[TreeNode]) -> int:
        best = float('-inf')
        def gain(node):
            nonlocal best
            if not node:
                return 0
            l = max(gain(node.left), 0)
            r = max(gain(node.right), 0)
            best = max(best, node.val + l + r)
            return node.val + max(l, r)
        gain(root)
        return best
""",
    "LC144": """class Solution:
    def preorderTraversal(self, root: Optional[TreeNode]) -> List[int]:
        res = []
        def dfs(node):
            if not node:
                return
            res.append(node.val)
            dfs(node.left)
            dfs(node.right)
        dfs(root)
        return res
""",
    "LC145": """class Solution:
    def postorderTraversal(self, root: Optional[TreeNode]) -> List[int]:
        res = []
        def dfs(node):
            if not node:
                return
            dfs(node.left)
            dfs(node.right)
            res.append(node.val)
        dfs(root)
        return res
""",
    "LC222": """class Solution:
    def countNodes(self, root: Optional[TreeNode]) -> int:
        if not root:
            return 0
        return 1 + self.countNodes(root.left) + self.countNodes(root.right)
""",
    "LC337": """class Solution:
    def rob(self, root: Optional[TreeNode]) -> int:
        def dfs(node):
            if not node:
                return (0, 0)
            l0, l1 = dfs(node.left)
            r0, r1 = dfs(node.right)
            return (node.val + l1 + r1, max(l0, l1) + max(r0, r1))
        return max(dfs(root))
""",
}


# ── 错误实现（应有区分度，不能 AC） ─────────────────────────────

WRONG_SOLUTION = """class Solution:
    def __init__(self):
        pass
"""


def _to_code_question(q: dict) -> CodeQuestion:
    meta = q["code"]
    return CodeQuestion(
        id=q["id"],
        title=q["question"].split("\n")[0],
        description=q["question"],
        function_signature=meta.get("function_signature", ""),
        example_input="",
        example_output="",
        test_cases=[TestCase(**tc) for tc in meta.get("test_cases", [])],
    )


async def _verify(q: dict) -> tuple[bool, str]:
    """返回 (通过?, 说明)"""
    meta = q["code"]
    tcs = meta.get("test_cases", [])
    if not tcs:
        return True, "无判题用例（走 LLM 评估）"
    ref = SOLUTIONS.get(q["id"])
    if ref is None:
        # 未写参考实现的旧用例（首期 50 道已单独验证过），不算失败
        return True, "SKIP（未写参考实现）"
    cq = _to_code_question(q)

    ok = await run_judge(ref, cq)
    if not ok.passed:
        first = ok.details[0] if ok.details else {}
        return False, f"参考实现未 AC（{ok.verdict} {ok.passed_tests}/{ok.total_tests}）: {first}"

    # 区分度：错误实现（同名方法不存在）必须非 AC
    wrong = await run_judge(WRONG_SOLUTION, cq)
    if wrong.passed:
        return False, "错误实现被判 AC，无区分度"
    return True, f"AC（{ok.passed_tests}/{ok.total_tests}），区分度 OK"


async def main() -> int:
    all_ok = True
    n_with_cases = 0
    for q in LC_QUESTIONS:
        if q["code"].get("test_cases"):
            n_with_cases += 1
        ok, msg = await _verify(q)
        status = "✅" if ok else "❌"
        print(f"{status} {q['id']} {q['question'].split(chr(10))[0][:48]:<52} {msg}")
        all_ok = all_ok and ok
    print(f"\n带判题用例: {n_with_cases}/{len(LC_QUESTIONS)}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
