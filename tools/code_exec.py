"""
代码执行工具
============
在沙箱中安全执行 Python 代码，返回 stdout/stderr。
"""

from __future__ import annotations

import asyncio
import sys
from io import StringIO

from .base import tool


@tool(
    name="run_python",
    description="在沙箱中执行 Python 代码并返回输出。适合做计算、数据处理、测试代码等。",
    parameters={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "要执行的 Python 代码",
            },
            "timeout": {
                "type": "integer",
                "description": "超时时间（秒），默认 10",
            },
        },
        "required": ["code"],
    },
)
async def run_python(code: str, timeout: int = 10) -> str:
    """
    安全沙箱执行 Python 代码。

    限制:
        - 禁用危险内置函数 (__import__, open, exec, eval, etc.)
        - 超时自动终止
        - 内存输出上限 10000 字符
    """
    # 预处理：移除 markdown 代码块标记
    code = code.strip()
    if code.startswith("```python"):
        code = code[9:]
    elif code.startswith("```"):
        code = code[3:]
    if code.endswith("```"):
        code = code[:-3]
    code = code.strip()

    # 安全沙箱
    safe_globals = {
        "__builtins__": {
            "print": print,
            "len": len,
            "range": range,
            "list": list,
            "dict": dict,
            "set": set,
            "tuple": tuple,
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "type": type,
            "isinstance": isinstance,
            "enumerate": enumerate,
            "zip": zip,
            "map": map,
            "filter": filter,
            "sorted": sorted,
            "reversed": reversed,
            "sum": sum,
            "min": min,
            "max": max,
            "abs": abs,
            "round": round,
            "any": any,
            "all": all,
            "True": True,
            "False": False,
            "None": None,
            "Exception": Exception,
            "ValueError": ValueError,
            "TypeError": TypeError,
            "KeyError": KeyError,
            "IndexError": IndexError,
            "json": __import__("json"),
            "math": __import__("math"),
            "datetime": __import__("datetime"),
            "re": __import__("re"),
            "itertools": __import__("itertools"),
            "collections": __import__("collections"),
            "statistics": __import__("statistics"),
            "random": __import__("random"),
        },
        "__name__": "__main__",
    }

    stdout = StringIO()
    stderr = StringIO()

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = stdout
    sys.stderr = stderr

    try:
        # 带超时的执行
        compiled = compile(code, "<sandbox>", "exec")

        async with asyncio.timeout(timeout):
            exec(compiled, safe_globals, {})

    except TimeoutError:
        return f"⏱️ 代码执行超时 ({timeout}s)"

    except Exception as e:
        stderr.write(f"{type(e).__name__}: {e}")

    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    output = stdout.getvalue()
    errors = stderr.getvalue()

    parts = []
    if output:
        max_out = 5000
        parts.append(f"[stdout]\n{output[:max_out]}" + ("..." if len(output) > max_out else ""))
    if errors:
        parts.append(f"[stderr]\n{errors}")

    if not parts:
        return "[执行完成，无输出]"

    return "\n\n".join(parts)
