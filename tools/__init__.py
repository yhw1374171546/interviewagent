"""
工具系统
========
提供可插拔的工具注册与执行机制。

使用方式:
    from tools.base import tool, ToolRegistry

    registry = ToolRegistry()

    @tool(description="搜索网页")
    async def web_search(query: str) -> str:
        '''搜索互联网并返回结果摘要'''
        ...

    registry.register(web_search)
"""

from .base import ToolRegistry, tool

__all__ = ["ToolRegistry", "tool"]
