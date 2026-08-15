"""
进程级共享 Embedding 模型（性能优化）
====================================
所有用到 all-MiniLM-L6-v2 的模块（ChromaDB 向量记忆、JD 语义缓存）共享
同一个 SentenceTransformer 实例 — 冷启动只加载一次模型（省 2-4s），
避免每个模块各自 new 一个模型实例。

使用:
    from core.embedding import get_sentence_model
    vecs = get_sentence_model().encode(["文本"])

注意:
    - 模型加载失败返回 None（调用方各自降级，不阻塞主流程）
    - 不主动触网：模型文件来自本地缓存（HF_HUB_OFFLINE 由启动方设置，
      缓存缺失时加载失败 → 降级，绝不阻塞）
"""

from __future__ import annotations

_sentence_model = None
_model_failed = False


def get_sentence_model(model_name: str = "all-MiniLM-L6-v2"):
    """进程级共享的 SentenceTransformer 实例（懒加载，失败返回 None）"""
    global _sentence_model, _model_failed
    if _sentence_model is None and not _model_failed:
        try:
            from sentence_transformers import SentenceTransformer

            _sentence_model = SentenceTransformer(model_name)
        except Exception:
            _model_failed = True  # 模型不可用（缓存缺失/网络）→ 调用方降级
    return _sentence_model
