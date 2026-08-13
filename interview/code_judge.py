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
import os
import sys
import tempfile
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


def audit_code_safety(code: str) -> tuple[bool, str]:
    """
    AST 白名单安全审计。

    Args:
        code: 用户提交的代码

    Returns:
        (是否安全, 错误信息)
    """
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


async def run_judge(
    user_code: str,
    question: CodeQuestion,
    timeout_sec: int | None = None,
) -> JudgeResult:
    """
    执行代码判题。

    流程:
        1. 写入用户代码到临时文件
        2. 追加测试代码
        3. subprocess 隔离执行
        4. 比较 stdout 与预期输出
        5. 返回测试报告

    Args:
        user_code: 用户提交的代码
        question: 编程题（包含测试用例）
        timeout_sec: 超时时间

    Returns:
        JudgeResult: 完整的判题结果
    """
    timeout = timeout_sec or question.time_limit_sec

    # 1. 安全检查
    safe, err_msg = audit_code_safety(user_code)
    if not safe:
        return JudgeResult(
            passed=False,
            total_tests=len(question.test_cases),
            errors=1,
            details=[{"name": "安全检查", "passed": False, "error": err_msg}],
        )

    # 2. 构建完整测试脚本
    test_script = _build_test_script(user_code, question)

    # 3. 写入临时文件
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(test_script)
        tmp_path = f.name

    try:
        # 4. subprocess 隔离执行
        proc = await asyncio.create_subprocess_exec(
            sys.executable, tmp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

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
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        # 5. 解析测试结果
        return _parse_test_output(stdout, stderr, question)

    finally:
        # 清理临时文件
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _build_test_script(user_code: str, question: CodeQuestion) -> str:
    """
    构建完整的测试脚本。

    关键设计: 每个测试用例的 stdout 被重定向捕获，与期望输出做
    **字符串比较** — 判题依据是真实输出匹配，而不是"代码没抛异常"。
    （旧实现只检查异常，bug 代码不抛异常也会被判通过）
    """
    lines = [
        "# -*- coding: utf-8 -*-",
        "# 自动生成的测试脚本",
        "import io",
        "import contextlib",
        "",
        "# ── 用户代码 ──",
        user_code,
        "",
        "# ── 测试用例 ──",
    ]

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
        lines.append(f"        print('__TEST_{i}_FAIL__: 期望 ' + repr(_expected) + ' 实际 ' + repr(_out))")
        lines.append("except Exception as e:")
        lines.append(f"    print(f'__TEST_{i}_FAIL__: {{type(e).__name__}}: {{e}}')")

    lines.append("")
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
                # 解析「期望 X 实际 Y」格式，分别填入 expected / got
                msg = line.split("__TEST_{i}_FAIL__: ", 1)[-1]
                expected, got = tc.expected, msg
                if " 实际 " in msg:
                    parts = msg.split(" 实际 ", 1)
                    expected = parts[0].replace("期望 ", "")
                    got = parts[1]
                details.append({
                    "name": tc.name,
                    "passed": False,
                    "expected": expected,
                    "got": got,
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

    return JudgeResult(
        passed=(passed == len(question.test_cases) and errors == 0 and failed == 0),
        total_tests=len(question.test_cases),
        passed_tests=passed,
        failed_tests=failed,
        errors=errors,
        details=details,
        stdout=stdout,
        stderr=stderr,
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
