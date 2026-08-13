"""
向量记忆存储
============
基于 ChromaDB 的长期记忆，支持语义检索。
"""

from __future__ import annotations

import chromadb
from chromadb.utils import embedding_functions


class VectorMemory:
    """
    向量记忆库。

    用于存储和检索对话中的关键信息，实现长期记忆。

    使用:
        vm = VectorMemory()
        vm.remember("用户喜欢用 Rust 写后端", metadata={"category": "preference"})
        results = vm.recall("用户喜欢什么编程语言？", top_k=3)
    """

    def __init__(
        self,
        collection_name: str = "agent_memory",
        persist_dir: str = "./data/chroma",
        embedding_model: str = "all-MiniLM-L6-v2",
    ):
        """
        Args:
            collection_name: ChromaDB collection 名称
            persist_dir: 持久化目录
            embedding_model: Sentence-Transformers 模型名
        """
        self.client = chromadb.PersistentClient(path=persist_dir)

        # 使用本地 embedding 模型（隐私友好）
        self.ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=embedding_model,
        )

        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=self.ef,
            metadata={"hnsw:space": "cosine"},
        )

    def remember(
        self,
        content: str,
        metadata: dict | None = None,
        doc_id: str | None = None,
    ) -> str:
        """
        存储一条记忆。

        Args:
            content: 记忆内容
            metadata: 附加元数据（类别、时间戳等）
            doc_id: 可选的文档 ID（自动生成）

        Returns:
            文档 ID
        """
        import uuid
        from datetime import datetime

        doc_id = doc_id or str(uuid.uuid4())[:8]
        meta = metadata or {}
        meta.setdefault("timestamp", datetime.now().isoformat())
        meta.setdefault("type", "memory")

        self.collection.add(
            documents=[content],
            metadatas=[meta],
            ids=[doc_id],
        )

        return doc_id

    def recall(
        self,
        query: str,
        top_k: int = 5,
        filter_meta: dict | None = None,
    ) -> list[dict]:
        """
        语义检索相关记忆。

        Args:
            query: 查询文本
            top_k: 返回条数
            filter_meta: 按元数据过滤

        Returns:
            [{id, content, metadata, score}, ...]
        """
        kwargs = dict(
            query_texts=[query],
            n_results=top_k,
        )
        if filter_meta:
            kwargs["where"] = filter_meta

        results = self.collection.query(**kwargs)

        memories = []
        if results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                memories.append({
                    "id": doc_id,
                    "content": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                    "distance": results["distances"][0][i] if results["distances"] else 0,
                })

        return memories

    def forget(self, doc_id: str) -> None:
        """删除一条记忆"""
        self.collection.delete(ids=[doc_id])

    def clear(self) -> None:
        """清空所有记忆"""
        ids = self.collection.get()["ids"]
        if ids:
            self.collection.delete(ids=ids)

    def stats(self) -> dict:
        """记忆统计"""
        count = self.collection.count()
        return {
            "total_memories": count,
            "collection": self.collection.name,
        }
