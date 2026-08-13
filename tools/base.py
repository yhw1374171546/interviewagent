"""
工具基类与注册器
================

工具注册器负责：
1. 管理和注册工具函数
2. 自动生成 Function Calling 所需的 JSON Schema
3. 执行工具调用并返回结果

自定义工具示例:
    @tool(
        name="calculator",
        description="执行数学运算",
        parameters={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "数学表达式"}
            },
            "required": ["expression"],
        },
    )
    async def calculator(expression: str) -> str:
        return str(eval(expression))
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from core.llm import ToolDefinition


def tool(
    _func: Callable | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> Callable:
    """
    工具装饰器。

    自动从函数签名推断 JSON Schema，也支持手动指定。

    使用方式:
        @tool(name="web_search", description="搜索网页")
        async def web_search(query: str, num: int = 5) -> str: ...

        # 或手动指定 JSON Schema
        @tool(name="search", description="搜索", parameters={...})
        async def search(query: str) -> str: ...
    """
    def decorator(fn: Callable) -> Callable:
        tool_name = name or fn.__name__
        tool_desc = description or fn.__doc__ or ""

        # 自动推断参数 Schema
        if parameters:
            schema = parameters
        else:
            schema = _infer_schema(fn)

        # 附加元信息
        fn._tool_meta = {
            "name": tool_name,
            "description": tool_desc.strip(),
            "parameters": schema,
        }
        return fn

    if _func is not None:
        return decorator(_func)
    return decorator


class ToolRegistry:
    """
    工具注册中心。

    注册工具 → 生成 Function Calling Schema → 执行

    使用:
        registry = ToolRegistry()
        registry.register(my_tool)
        result = await registry.execute("my_tool", arg1="value")
    """

    def __init__(self):
        self._tools: dict[str, Callable] = {}

    def register(self, fn: Callable) -> None:
        """注册一个工具函数"""
        meta = getattr(fn, "_tool_meta", None)
        if meta is None:
            # 自动包装
            fn = tool(fn)
            meta = fn._tool_meta
        name = meta["name"]
        self._tools[name] = fn

    def register_many(self, *fns: Callable) -> None:
        """批量注册"""
        for fn in fns:
            self.register(fn)

    async def execute(self, name: str, **kwargs: Any) -> Any:
        """执行指定工具"""
        if name not in self._tools:
            raise ValueError(f"未注册的工具: {name}，可用: {list(self._tools)}")

        fn = self._tools[name]
        result = fn(**kwargs)

        # 支持 async 和 sync
        if inspect.iscoroutine(result):
            result = await result

        return result

    def to_definitions(self) -> list[ToolDefinition]:
        """生成 LLM Function Calling 所需的工具定义列表"""
        definitions = []
        for name, fn in self._tools.items():
            meta = fn._tool_meta
            definitions.append(ToolDefinition(
                name=meta["name"],
                description=meta["description"],
                parameters=meta["parameters"],
            ))
        return definitions

    def tool_names(self) -> list[str]:
        """已注册的工具名称列表"""
        return list(self._tools.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)


# ── Schema 自动推断 ───────────────────────────────────────────

_PYTHON_TO_JSON_TYPE = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _infer_schema(fn: Callable) -> dict[str, Any]:
    """从函数签名自动推断 JSON Schema"""
    sig = inspect.signature(fn)
    properties = {}
    required = []

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue

        ann = param.annotation
        json_type = "string"

        if ann is not inspect.Parameter.empty:
            json_type = _PYTHON_TO_JSON_TYPE.get(ann, "string")

        properties[param_name] = {
            "type": json_type,
            "description": f"参数: {param_name}",
        }

        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }
