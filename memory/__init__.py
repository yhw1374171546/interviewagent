"""
记忆模块
========
管理对话上下文和长期记忆。

注意: VectorMemory 依赖可选的 chromadb + sentence-transformers，
__init__.py 不做顶层导入（避免缺依赖时 import memory 崩溃），
通过 get_vector_memory() 懒加载，主链路降级逻辑见
interview/memory_context.py。
"""

from .context import ContextManager
from .summarizer import ConversationSummarizer

__all__ = ["ContextManager", "ConversationSummarizer"]


def get_vector_memory(**kwargs):
    """懒加载 VectorMemory（依赖 chromadb，缺失时抛 ImportError 由调用方降级）"""
    from .vector_store import VectorMemory

    return VectorMemory(**kwargs)
