"""
代码裁判
========
真正的沙箱判题系统 — 执行代码 + 运行测试用例 + 返回测试报告。

不是"把代码发给 LLM 让它评价"。
流程: 用户代码 → AST 安全检查 → 沙箱执行 → 运行测试用例 → 输出通过/失败详情

技术亮点:
- AST 白名单检查：只允许安全 AST 节点
- subprocess 隔离：超时 + 内存限制
- 测试用例驱动：不是 LLM 主观判断，而是真实的 pass/fail
- CI 风格的输出：像 GitHub Actions 一样的测试报告
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field

# ── 测试用例定义 ──────────────────────────────────────────────

@dataclass
class TestCase:
    """一个测试用例"""
    name: str                # 测试名称
    input_code: str          # 前置代码（准备数据/函数调用）
    expected: str            # 期望输出（stdout 或返回值）
    check_type: str = "stdout"  # "stdout" | "return" | "exception"


@dataclass
class CodeQuestion:
    """一道编程题（包含题目描述 + 测试用例）"""
    id: str
    title: str
    description: str
    function_signature: str   # 函数签名
    example_input: str
    example_output: str
    test_cases: list[TestCase] = field(default_factory=list)
    difficulty: int = 3
    time_limit_sec: int = 5
    tags: list[str] = field(default_factory=list)


# ── 预置编程题 ────────────────────────────────────────────────

PRESET_CODE_QUESTIONS: list[CodeQuestion] = [
    CodeQuestion(
        id="COD_LRU",
        title="LRU 缓存",
        description="实现一个 LRU (Least Recently Used) 缓存类。get(key) 获取值（不存在返回 -1），put(key, value) 写入值。容量满时淘汰最久未使用的。要求 get 和 put 时间复杂度 O(1)。",
        function_signature="class LRUCache:\n    def __init__(self, capacity: int): ...\n    def get(self, key: int) -> int: ...\n    def put(self, key: int, value: int) -> None: ...",
        example_input='cache = LRUCache(2)\ncache.put(1, 1)\ncache.put(2, 2)\nprint(cache.get(1))  # 1\ncache.put(3, 3)  # 淘汰 key 2\nprint(cache.get(2))  # -1',
        example_output="1\n-1",
        difficulty=3,
        tags=["数据结构", "哈希表", "双向链表"],
        test_cases=[
            TestCase(
                name="基本 put/get",
                input_code="cache = LRUCache(2)\ncache.put(1, 1)\ncache.put(2, 2)\nprint(cache.get(1))\nprint(cache.get(2))",
                expected="1\n2",
            ),
            TestCase(
                name="容量溢出淘汰",
                input_code="cache = LRUCache(2)\ncache.put(1, 1)\ncache.put(2, 2)\ncache.put(3, 3)\nprint(cache.get(1))\nprint(cache.get(2))\nprint(cache.get(3))",
                # 正确行为: put(3) 淘汰最久未使用的 1 → {2:2, 3:3}
                # get(1)→-1, get(2)→2, get(3)→3
                expected="-1\n2\n3",
            ),
            TestCase(
                name="更新已存在的key",
                input_code="cache = LRUCache(2)\ncache.put(1, 1)\ncache.put(2, 2)\ncache.put(1, 10)\nprint(cache.get(1))\nprint(cache.get(2))",
                expected="10\n2",
            ),
            TestCase(
                name="容量为1的边界情况",
                input_code="cache = LRUCache(1)\ncache.put(1, 1)\ncache.put(2, 2)\nprint(cache.get(1))\nprint(cache.get(2))",
                expected="-1\n2",
            ),
            TestCase(
                name="get后淘汰最久未使用",
                # 关键: get(1) 后 1 变为最近使用，put(3) 应淘汰 2 而非 1
                # 不维护 get 顺序的实现（如 dict+order 列表）会在这里暴露 bug
                input_code="cache = LRUCache(2)\ncache.put(1, 1)\ncache.put(2, 2)\nprint(cache.get(1))\ncache.put(3, 3)\nprint(cache.get(1))\nprint(cache.get(2))\nprint(cache.get(3))",
                expected="1\n1\n-1\n3",
            ),
        ],
    ),
    CodeQuestion(
        id="COD_TWOSUM",
        title="两数之和",
        description="给定一个整数数组 nums 和一个目标值 target，返回两个数的索引，使它们相加等于 target。假设每个输入只有一个答案，同一个元素不能使用两次。",
        function_signature="def two_sum(nums: list[int], target: int) -> list[int]: ...",
        example_input='print(two_sum([2, 7, 11, 15], 9))',
        example_output="[0, 1]",
        difficulty=1,
        tags=["算法", "哈希表", "数组"],
        test_cases=[
            TestCase(
                name="基础用例",
                input_code="print(two_sum([2, 7, 11, 15], 9))",
                expected="[0, 1]",
            ),
            TestCase(
                name="重复元素",
                input_code="print(two_sum([3, 3], 6))",
                expected="[0, 1]",
            ),
            TestCase(
                name="无序输入",
                input_code="result = two_sum([3, 2, 4], 6)\nprint(sorted(result))",
                expected="[1, 2]",
            ),
        ],
    ),
    CodeQuestion(
        id="COD_DEDUP",
        title="数组去重（保持顺序）",
        description="给定一个列表，去除重复元素但保持原始顺序。要求时间复杂度 O(n)。",
        function_signature="def dedup(lst: list) -> list: ...",
        example_input='print(dedup([1, 2, 2, 3, 1, 4]))',
        example_output="[1, 2, 3, 4]",
        difficulty=1,
        tags=["算法", "哈希表"],
        test_cases=[
            TestCase(
                name="正常去重",
                input_code="print(dedup([1, 2, 2, 3, 1, 4]))",
                expected="[1, 2, 3, 4]",
            ),
            TestCase(
                name="全部重复",
                input_code="print(dedup([1, 1, 1, 1]))",
                expected="[1]",
            ),
            TestCase(
                name="无重复",
                input_code="print(dedup([1, 2, 3, 4]))",
                expected="[1, 2, 3, 4]",
            ),
        ],
    ),
]


# ── AST 安全审计 ──────────────────────────────────────────────

# 白名单：只允许这些 AST 节点
ALLOWED_AST_NODES = {
    # 基础
    ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
    ast.Return, ast.Pass, ast.Expr,
    # 赋值
    ast.Assign, ast.AugAssign, ast.AnnAssign,
    # 控制流
    ast.If, ast.For, ast.While, ast.Break, ast.Continue,
    ast.Try, ast.ExceptHandler, ast.Raise, ast.With, ast.AsyncWith,
    # 表达式
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
    ast.Call, ast.Attribute, ast.Subscript, ast.Slice,
    ast.Name, ast.Constant, ast.List, ast.Dict, ast.Set, ast.Tuple,
    ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp,
    ast.Lambda, ast.IfExp,  # 三元表达式 a if cond else b
    # 运算符
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd,
    ast.Eq, ast.NotEq, ast.Lt, ast.Gt, ast.LtE, ast.GtE,
    ast.And, ast.Or, ast.Not, ast.In, ast.NotIn,
    ast.Is, ast.IsNot,  # 身份比较（is / is not）
    ast.comprehension,  # 列表/字典/集合推导式的子节点（for x in ...）
    ast.Nonlocal,       # 嵌套函数 nonlocal 声明（闭包计数器等）
    # 其他
    ast.Load, ast.Store, ast.Del, ast.Delete,
    ast.arg, ast.keyword,
    ast.alias, ast.Import, ast.ImportFrom,
    ast.arguments, ast.JoinedStr, ast.FormattedValue,
}

# 允许的 import 模块（白名单）
ALLOWED_IMPORTS = {
    # 标准库
    "sys", "os.path", "math", "itertools", "functools", "collections",
    "heapq", "bisect", "random", "json", "datetime", "re", "typing",
    "dataclasses", "enum", "copy", "hashlib", "uuid", "statistics",
    "abc", "dataclasses",
    # 常用库
    "numpy", "pandas",
}

# 禁止调用的函数
FORBIDDEN_CALLS = {
    "eval", "exec", "compile", "open", "__import__",
    "subprocess", "os.system", "os.popen", "os.spawn",
    "shutil.rmtree", "shutil.move",
    "sys.exit",
}


# ── 多语言支持 ──────────────────────────────────────────────────
# Python 用 AST 白名单（强）；C++ 无内置 AST 解析器，用黑名单 + 超时
# （demo 级简化安全，重点是超时与输出比对）。

LANGUAGES: dict[str, dict] = {
    "python": {"ext": ".py", "label": "Python", "compiled": False},
    "cpp": {"ext": ".cpp", "label": "C++", "compiled": True},
}

# C++ 黑名单（聚焦文件/进程/网络等危险 API）
_CPP_FORBIDDEN = [
    "system(", "popen(", "fork(", "exec", "socket(", "ofstream", "ifstream",
    "fopen(", "freopen(", "mmap(", "dlopen", "setuid", "unistd.h",
]

# ── LeetCode 节点工具（判题注入） ────────────────────────────────
# 链表/树题的测试用例需要真实的 ListNode/TreeNode 对象 + 数组↔对象转换。
# 这段代码在用户代码之前注入（用户代码里的类型注解 `Optional[ListNode]`
# 在 def 时求值，必须先有类定义）。
NODE_UTILS_SOURCE = """# ── LeetCode 节点工具（判题注入）──
from typing import Optional, List

class ListNode:
    def __init__(self, val=0, next=None):
        self.val = val
        self.next = next

class TreeNode:
    def __init__(self, val=0, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right

def list_to_linkedlist(arr):
    \"\"\"数组 → 链表（LeetCode 风格）\"\"\"
    dummy = ListNode(0)
    cur = dummy
    for v in arr:
        cur.next = ListNode(v)
        cur = cur.next
    return dummy.next

def linkedlist_to_list(head):
    \"\"\"链表 → 数组（用于输出比对，防环）\"\"\"
    res, seen = [], set()
    while head is not None and id(head) not in seen:
        seen.add(id(head))
        res.append(head.val)
        head = head.next
    return res

def list_to_tree(arr):
    \"\"\"层序数组 → 二叉树（null 表示空节点）\"\"\"
    if not arr:
        return None
    root = TreeNode(arr[0])
    queue = [root]
    i = 1
    while queue and i < len(arr):
        node = queue.pop(0)
        if i < len(arr) and arr[i] is not None:
            node.left = TreeNode(arr[i])
            queue.append(node.left)
        i += 1
        if i < len(arr) and arr[i] is not None:
            node.right = TreeNode(arr[i])
            queue.append(node.right)
        i += 1
    return root

def tree_to_list(root):
    \"\"\"二叉树 → 层序数组（尾部 null 省略，LeetCode 风格）\"\"\"
    if root is None:
        return []
    res, queue = [], [root]
    while queue:
        node = queue.pop(0)
        if node is None:
            res.append(None)
            continue
        res.append(node.val)
        queue.append(node.left)
        queue.append(node.right)
    while res and res[-1] is None:
        res.pop()
    return res

def trees_to_list(trees):
    \"\"\"List[TreeNode] → List[层序数组]（如 generateTrees）\"\"\"
    return [tree_to_list(t) for t in trees]
"""


def _compile_command(language: str, src_path: str, exe_path: str) -> list[str] | None:
    """返回编译命令；解释型语言返回 None（无需编译）"""
    if language == "cpp":
        return ["g++", "-std=c++17", src_path, "-o", exe_path]
    return None


def _runner_command(language: str, src_path: str, exe_path: str) -> list[str]:
    """返回执行命令（解释型直接跑源码，编译型跑编译产物）"""
    if language == "python":
        return [sys.executable, src_path]
    if language == "cpp":
        return [exe_path]
    raise ValueError(f"不支持的编程语言: {language}")


def audit_code_safety(code: str, language: str = "python") -> tuple[bool, str]:
    """
    代码安全审计。

    - python: AST 白名单（强）
    - cpp: 黑名单 + 超时（demo 级简化）

    Args:
        code: 用户提交的代码
        language: 编程语言（python / cpp）

    Returns:
        (是否安全, 错误信息)
    """
    if language == "cpp":
        return _audit_cpp(code)

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"语法错误: {e}"

    for node in ast.walk(tree):
        # 检查节点类型
        if type(node) not in ALLOWED_AST_NODES:
            return False, f"不允许的语法结构: {type(node).__name__}"

        # 检查 import 白名单
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                base = node.module or ""
                modules = [f"{base}.{alias.name}" if base else alias.name for alias in node.names]

            for mod in modules:
                if not any(mod == allowed or mod.startswith(allowed + ".") for allowed in ALLOWED_IMPORTS):
                    # 检查是否标准库（不在白名单但在 sys.stdlib_module_names 中的也放行）
                    if mod.split(".")[0] in sys.stdlib_module_names:
                        continue
                    return False, f"不允许导入的模块: {mod}"

        # 检查禁止的函数调用
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
                return False, f"禁止调用的函数: {node.func.id}"
            if isinstance(node.func, ast.Attribute):
                full_name = _get_attr_name(node.func)
                for forbidden in FORBIDDEN_CALLS:
                    if full_name and full_name.endswith(forbidden):
                        return False, f"禁止调用的函数: {full_name}"

    return True, ""


def _audit_cpp(code: str) -> tuple[bool, str]:
    """C++ 黑名单安全审计（demo 级，真正的隔离靠 subprocess 超时）"""
    lowered = code.lower()
    for forbidden in _CPP_FORBIDDEN:
        if forbidden in lowered:
            return False, f"禁止使用的语法/模块: {forbidden}"
    return True, ""


def _get_attr_name(node: ast.Attribute) -> str:
    """递归获取 ast.Attribute 的完整名称，如 a.b.c"""
    parts = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


# ── 沙箱执行器 ────────────────────────────────────────────────

@dataclass
class JudgeResult:
    """判题结果"""
    passed: bool
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    errors: int = 0
    details: list[dict] = field(default_factory=list)  # [{name, passed, expected, got, error}]
    stdout: str = ""
    stderr: str = ""
    execution_time_ms: int = 0
    # 判题结论（LeetCode 风格）: AC / WA / TLE / CE / RE / SE
    verdict: str = ""


async def run_judge(
    user_code: str,
    question: CodeQuestion,
    timeout_sec: int | None = None,
    language: str = "python",
    mode: str = "core",
) -> JudgeResult:
    """
    执行代码判题。

    两种模式:
        - core（核心代码）: 用户只写函数/类定义，测试框架拼接调用代码执行
        - acm（完整程序）: 用户写完整程序（读 stdin 写 stdout），测试输入经 stdin 传入

    流程:
        1. 安全检查（Python=AST 白名单，C++=黑名单+超时）
        2. 写入用户代码到临时文件（编译型语言先编译）
        3. 追加测试代码（core）或经 stdin 喂输入（acm）
        4. subprocess 隔离执行
        5. 比较 stdout 与预期输出
        6. 返回测试报告

    Args:
        user_code: 用户提交的代码
        question: 编程题（包含测试用例）
        timeout_sec: 超时时间
        language: 编程语言（python / cpp）
        mode: 判题模式（core / acm）

    Returns:
        JudgeResult: 完整的判题结果
    """
    timeout = timeout_sec or question.time_limit_sec

    # 0. 无测试用例 → 不执行（0/0 判 AC 是假阳性，必须由调用方降级处理）
    if not question.test_cases:
        return JudgeResult(
            passed=False,
            total_tests=0,
            errors=1,
            details=[{
                "name": "无测试用例",
                "passed": False,
                "error": "该题没有自动判题用例，无法沙箱判题（应走 LLM 代码评审）",
            }],
            verdict="SE",
        )

    # 1. 语言支持检查
    if language not in LANGUAGES:
        return JudgeResult(
            passed=False,
            total_tests=len(question.test_cases),
            errors=1,
            details=[{"name": "不支持的语言", "passed": False, "error": f"不支持: {language}"}],
            verdict="SE",
        )

    # 0.5 ACM 完整程序模式 → 独立执行路径
    if mode == "acm":
        return await _run_acm(user_code, question, language, timeout)

    # 1. 安全检查
    safe, err_msg = audit_code_safety(user_code, language)
    if not safe:
        return JudgeResult(
            passed=False,
            total_tests=len(question.test_cases),
            errors=1,
            details=[{"name": "安全检查", "passed": False, "error": err_msg}],
            verdict="SE",
        )

    # 2. 构建完整测试脚本
    test_script = _build_test_script(user_code, question, language)

    # 3. 写入临时源码文件
    ext = LANGUAGES[language]["ext"]
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=ext, delete=False, encoding="utf-8"
    ) as f:
        f.write(test_script)
        src_path = f.name

    exe_path = None
    try:
        # 4. 编译（编译型语言；解释型语言跳过）
        compile_cmd = _compile_command(language, src_path, src_path + ".exe")
        if compile_cmd is not None:
            exe_path = src_path + ".exe"
            compile_proc = await asyncio.create_subprocess_exec(
                *compile_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, compile_err = await compile_proc.communicate()
            if compile_proc.returncode != 0:
                err_text = compile_err.decode("utf-8", errors="replace").strip()
                return JudgeResult(
                    passed=False,
                    total_tests=len(question.test_cases),
                    errors=1,
                    details=[{"name": "编译错误", "passed": False, "error": err_text[:300]}],
                    stderr=err_text[:500],
                    verdict="CE",
                )

        # 5. subprocess 隔离执行
        run_cmd = _runner_command(language, src_path, exe_path or "")
        proc = await asyncio.create_subprocess_exec(
            *run_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        t0 = time.perf_counter()
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return JudgeResult(
                passed=False,
                total_tests=len(question.test_cases),
                errors=1,
                details=[{"name": "超时", "passed": False, "error": f"代码执行超过 {timeout} 秒"}],
                verdict="TLE",
            )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        # 6. 解析测试结果
        result = _parse_test_output(stdout, stderr, question)
        result.execution_time_ms = elapsed_ms
        return result

    finally:
        # 清理临时源码与编译产物
        for path in (src_path, exe_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


async def _run_acm(
    user_code: str,
    question: CodeQuestion,
    language: str,
    timeout: float,
) -> JudgeResult:
    """
    ACM 完整程序模式判题。

    与 core 模式的区别: 用户写的是完整程序（自己读 stdin、写 stdout），
    判题器把每个测试用例的 input_code 作为 stdin 喂给程序，比对 stdout。
    （牛客/传统 OJ 的提交方式）
    """
    safe, err_msg = audit_code_safety(user_code, language)
    if not safe:
        return JudgeResult(
            passed=False,
            total_tests=len(question.test_cases),
            errors=1,
            details=[{"name": "安全检查", "passed": False, "error": err_msg}],
            verdict="SE",
        )

    ext = LANGUAGES[language]["ext"]
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=ext, delete=False, encoding="utf-8"
    ) as f:
        f.write(user_code)
        src_path = f.name

    exe_path = None
    try:
        # 编译（编译型语言）
        compile_cmd = _compile_command(language, src_path, src_path + ".exe")
        if compile_cmd is not None:
            exe_path = src_path + ".exe"
            compile_proc = await asyncio.create_subprocess_exec(
                *compile_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, compile_err = await compile_proc.communicate()
            if compile_proc.returncode != 0:
                err_text = compile_err.decode("utf-8", errors="replace").strip()
                return JudgeResult(
                    passed=False,
                    total_tests=len(question.test_cases),
                    errors=1,
                    details=[{"name": "编译错误", "passed": False, "error": err_text[:300]}],
                    stderr=err_text[:500],
                    verdict="CE",
                )

        run_cmd = _runner_command(language, src_path, exe_path or "")

        details = []
        passed = 0
        failed = 0
        total_elapsed_ms = 0
        for i, tc in enumerate(question.test_cases):
            proc = await asyncio.create_subprocess_exec(
                *run_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            t0 = time.perf_counter()
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(input=tc.input_code.encode("utf-8")),
                    timeout=timeout,
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                failed += 1
                details.append({"name": tc.name, "passed": False, "error": "超时"})
                continue
            total_elapsed_ms += int((time.perf_counter() - t0) * 1000)

            out = stdout_bytes.decode("utf-8", errors="replace").strip()
            err = stderr_bytes.decode("utf-8", errors="replace").strip()
            if out == tc.expected:
                passed += 1
                details.append({"name": tc.name, "passed": True})
            elif err:
                # RE：程序运行时错误（stderr 有 traceback）
                failed += 1
                details.append({"name": tc.name, "passed": False, "error": err[:200]})
            else:
                failed += 1
                details.append({
                    "name": tc.name,
                    "passed": False,
                    "expected": tc.expected,
                    "got": out,
                })

        is_all_pass = passed == len(question.test_cases) and failed == 0
        if is_all_pass:
            verdict = "AC"
        elif any(d.get("error") == "超时" for d in details):
            verdict = "TLE"
        elif any("error" in d and "expected" not in d for d in details):
            verdict = "RE"
        else:
            verdict = "WA"

        return JudgeResult(
            passed=is_all_pass,
            total_tests=len(question.test_cases),
            passed_tests=passed,
            failed_tests=failed,
            errors=0,
            details=details,
            execution_time_ms=total_elapsed_ms,
            verdict=verdict,
        )

    finally:
        for path in (src_path, exe_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def _build_test_script(user_code: str, question: CodeQuestion, language: str = "python") -> str:
    """
    构建完整测试脚本（按语言分派）。

    关键设计: 每个测试用例的 stdout 被重定向捕获，与期望输出做
    **字符串比较** — 判题依据是真实输出匹配，而不是"代码没抛异常"。
    （旧实现只检查异常，bug 代码不抛异常也会被判通过）
    """
    if language == "cpp":
        return _build_cpp_script(user_code, question)
    return _build_python_script(user_code, question)


def _build_python_script(user_code: str, question: CodeQuestion) -> str:
    """构建 Python 测试脚本（重定向 stdout + markers）"""
    lines = [
        "# -*- coding: utf-8 -*-",
        "# 自动生成的测试脚本",
        "import io",
        "import contextlib",
        "",
    ]

    # 链表/树题需要节点工具（类定义 + 数组↔对象转换），注入到用户代码之前。
    # 用户代码里的 `Optional[ListNode]` 注解在 def 时求值，必须先有类定义。
    if _needs_node_utils(user_code, question):
        lines.append("# ── 节点工具（判题注入）──")
        lines.append(NODE_UTILS_SOURCE.strip())
        lines.append("")

    lines.append("# ── 用户代码 ──")
    lines.append(user_code)
    lines.append("")
    lines.append("# ── 测试用例 ──")

    for i, tc in enumerate(question.test_cases):
        expected_repr = repr(tc.expected)  # Python 源码形式，可直接嵌入脚本
        lines.append("")
        lines.append(f"# 测试 {i+1}: {tc.name}")
        lines.append("try:")
        lines.append("    _buf = io.StringIO()")
        lines.append("    with contextlib.redirect_stdout(_buf):")
        # 缩进测试代码（在 redirect_stdout 上下文内）
        for code_line in tc.input_code.strip().split("\n"):
            lines.append(f"        {code_line}")
        lines.append("    _out = _buf.getvalue().strip()")
        lines.append(f"    _expected = {expected_repr}")
        lines.append("    if _out == _expected:")
        lines.append(f"        print('__TEST_{i}_PASS__')")
        lines.append("    else:")
        lines.append(f"        print('__TEST_{i}_FAIL__: expected ' + repr(_expected) + ' actual ' + repr(_out))")
        lines.append("except Exception as e:")
        lines.append(f"    print(f'__TEST_{i}_FAIL__: {{type(e).__name__}}: {{e}}')")

    lines.append("")
    return "\n".join(lines)


def _needs_node_utils(user_code: str, question: CodeQuestion) -> bool:
    """是否需要注入节点工具（用户代码引用节点类型，或用例用了转换函数）"""
    if "ListNode" in user_code or "TreeNode" in user_code:
        return True
    for tc in question.test_cases:
        if any(k in tc.input_code for k in (
            "list_to_linkedlist", "linkedlist_to_list",
            "list_to_tree", "tree_to_list", "trees_to_list",
        )):
            return True
    return False


def _build_cpp_script(user_code: str, question: CodeQuestion) -> str:
    """构建 C++ 测试脚本（stringstream 重定向 cout + markers）"""
    lines = [
        "#include <bits/stdc++.h>",
        "using namespace std;",
        "",
        "// ── 用户代码 ──",
        user_code,
        "",
        "// ── 测试用例 ──",
        "int main() {",
    ]

    for i, tc in enumerate(question.test_cases):
        expected_lit = json.dumps(tc.expected, ensure_ascii=False)
        lines.append(f"    // 测试 {i+1}: {tc.name}")
        lines.append("    {")
        lines.append("        stringstream _buf;")
        lines.append("        streambuf* _old = cout.rdbuf(_buf.rdbuf());")
        for code_line in tc.input_code.strip().split("\n"):
            lines.append(f"        {code_line}")
        lines.append("        cout.rdbuf(_old);")
        lines.append("        string _out = _buf.str();")
        lines.append("        while (!_out.empty() && isspace((unsigned char)_out.back())) _out.pop_back();")
        lines.append(f"        string _expected = {expected_lit};")
        lines.append("        if (_out == _expected) {")
        lines.append(f'            cout << "__TEST_{i}_PASS__" << endl;')
        lines.append("        } else {")
        lines.append(f'            cout << "__TEST_{i}_FAIL__: expected " << _expected << " actual " << _out << endl;')
        lines.append("        }")
        lines.append("    }")

    lines.append("    return 0;")
    lines.append("}")
    return "\n".join(lines)


def _parse_test_output(stdout: str, stderr: str, question: CodeQuestion) -> JudgeResult:
    """解析测试输出"""
    details = []
    passed = 0
    failed = 0
    errors = 0

    for i, tc in enumerate(question.test_cases):
        pass_marker = f"__TEST_{i}_PASS__"
        fail_marker = f"__TEST_{i}_FAIL__"

        # 从 stdout 中查找测试标记行
        for line in stdout.split("\n"):
            if pass_marker in line:
                passed += 1
                details.append({"name": tc.name, "passed": True})
                break
            elif fail_marker in line:
                failed += 1
                # 解析 "expected X actual Y" 格式，分别填入 expected / got
                msg = line.split(f"__TEST_{i}_FAIL__: ", 1)[-1].strip()
                if " actual " in msg:
                    # WA：输出不匹配
                    parts = msg.split(" actual ", 1)
                    expected = parts[0].replace("expected ", "")
                    got = parts[1].strip()
                    details.append({
                        "name": tc.name,
                        "passed": False,
                        "expected": expected,
                        "got": got,
                    })
                else:
                    # RE：运行时错误（异常信息，如 NameError/TypeError）
                    details.append({
                        "name": tc.name,
                        "passed": False,
                        "error": msg,
                    })
                break

    # 特殊处理：如果找不到标记（脚本执行失败），检查 stderr
    if len(details) == 0:
        if stderr:
            errors = len(question.test_cases)
            details = [{"name": tc.name, "passed": False, "error": stderr[:200]} for tc in question.test_cases]
        elif stdout:
            # 用户代码可能有自己的 print 输出，默认全部失败
            details = [{
                "name": tc.name,
                "passed": False,
                "expected": tc.expected,
                "got": "测试框架无法解析输出",
            } for tc in question.test_cases]
            failed = len(question.test_cases)

    # 判题结论：AC / RE / WA
    is_all_pass = passed == len(question.test_cases) and errors == 0 and failed == 0
    if is_all_pass:
        verdict = "AC"
    elif any("error" in d and "expected" not in d for d in details):
        verdict = "RE"
    else:
        verdict = "WA"

    return JudgeResult(
        passed=is_all_pass,
        total_tests=len(question.test_cases),
        passed_tests=passed,
        failed_tests=failed,
        errors=errors,
        details=details,
        stdout=stdout,
        stderr=stderr,
        verdict=verdict,
    )


def format_judge_report(result: JudgeResult) -> str:
    """格式化判题报告（CI 风格）"""
    lines = [
        "=" * 50,
        f"  测试结果: {'✅ 全部通过' if result.passed else '❌ 未通过'}",
        f"  通过: {result.passed_tests}/{result.total_tests}",
        "=" * 50,
        "",
    ]

    for i, detail in enumerate(result.details):
        status = "✅" if detail.get("passed") else "❌"
        name = detail.get("name", f"测试 {i+1}")
        lines.append(f"  {status} {name}")

        if not detail.get("passed"):
            if "error" in detail:
                lines.append(f"     错误: {detail['error']}")
            else:
                lines.append(f"     期望: {detail.get('expected', 'N/A')}")
                lines.append(f"     实际: {detail.get('got', 'N/A')}")
        lines.append("")

    if result.stderr:
        lines.append("  [stderr]")
        lines.append(f"  {result.stderr[:500]}")

    return "\n".join(lines)
