"""
JD 语义缓存
===========
把 JD 解析结果按「语义相似度」缓存复用——同岗位多场面试时跳过重复解析，
省 LLM 调用（JD 解析的 LLM 兜底部分）+ 省规则计算。

为什么用「语义」而非精确匹配:
    JD 文本几乎不会完全相同（不同 HR 措辞不同），精确匹配命中率≈0；
    语义相似度能覆盖「换措辞但同义」的真实场景。

设计:
    - 存储: JSON 文件（data/cache/jd_cache.json），{jd_text, analysis, ts}
    - 检索: 嵌入（all-MiniLM-L6-v2，复用 chroma 的模型）→ 余弦相似度
    - 命中: 相似度 ≥ threshold（默认 0.9）→ 返回缓存 analysis（0 LLM）
    - 未命中: 正常解析 → 写回缓存
    - 容量: 最多保留 N 条（FIFO，防无限增长）

用法:
    from interview.semantic_cache import JDSemanticCache
    cache = JDSemanticCache(threshold=0.9)
    hit = cache.lookup(jd_text)          # → JDAnalysis | None
    cache.store(jd_text, analysis)
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .jd_parser import JDAnalysis

DEFAULT_CACHE_PATH = Path(__file__).parent.parent / "data" / "cache" / "jd_cache.json"
DEFAULT_MAX_ENTRIES = 50
DEFAULT_THRESHOLD = 0.9


def _jd_to_dict(analysis: JDAnalysis) -> dict:
    """JDAnalysis → dict（缓存存储用）"""
    return {
        "position": analysis.position,
        "experience": analysis.experience,
        "education": analysis.education,
        "required_skills": analysis.required_skills,
        "preferred_skills": analysis.preferred_skills,
        "soft_skills": analysis.soft_skills,
        "domain_knowledge": analysis.domain_knowledge,
        "responsibilities": analysis.responsibilities,
        "interview_focus": analysis.interview_focus,
    }


def _dict_to_jd(data: dict) -> JDAnalysis:
    """dict → JDAnalysis（缓存读取用）"""
    return JDAnalysis(
        position=data.get("position", ""),
        experience=data.get("experience", ""),
        education=data.get("education", ""),
        required_skills=list(data.get("required_skills", [])),
        preferred_skills=list(data.get("preferred_skills", [])),
        soft_skills=list(data.get("soft_skills", [])),
        domain_knowledge=list(data.get("domain_knowledge", [])),
        responsibilities=list(data.get("responsibilities", [])),
        interview_focus=list(data.get("interview_focus", [])),
    )


class _Embedder:
    """嵌入器（懒加载 sentence-transformers，失败降级为 None → 缓存关闭）"""

    def __init__(self):
        self._model = None
        self._failed = False

    def _load(self):
        if self._model is None and not self._failed:
            try:
                # 进程级共享模型（与 ChromaDB 记忆共用同一个实例，只加载一次）
                from core.embedding import get_sentence_model
                self._model = get_sentence_model("all-MiniLM-L6-v2")
                if self._model is None:
                    self._failed = True
            except Exception:
                self._failed = True  # 模型不可用 → 缓存降级关闭
        return self._model

    def embed(self, text: str) -> list[float] | None:
        model = self._load()
        if model is None:
            return None
        vec = model.encode([text])[0]
        return vec.tolist()


# 进程级共享嵌入器 — 多缓存实例复用同一个模型，只加载一次（省 2-3s 冷启动）
_shared_embedder: _Embedder | None = None


def _get_embedder() -> _Embedder:
    global _shared_embedder
    if _shared_embedder is None:
        _shared_embedder = _Embedder()
    return _shared_embedder


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度（a, b 为同维向量）"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class JDSemanticCache:
    """
    JD 语义缓存。

    Args:
        cache_path: 缓存文件路径（None 用默认 data/cache/jd_cache.json）
        threshold: 相似度命中阈值（0-1，默认 0.9）
        max_entries: 最大缓存条数（FIFO 淘汰）
    """

    def __init__(
        self,
        cache_path: str | None = None,
        threshold: float = DEFAULT_THRESHOLD,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ):
        self.cache_path = Path(cache_path) if cache_path else DEFAULT_CACHE_PATH
        self.threshold = threshold
        self.max_entries = max_entries
        self._embedder = _get_embedder()  # 进程级共享嵌入器（模型只加载一次）
        self._entries: list[dict] = []  # [{jd_text, analysis, ts, vec}]
        self._load()

    # ── 存储 ────────────────────────────────────────────

    def _load(self) -> None:
        """从磁盘加载缓存（损坏/缺失 → 空）"""
        try:
            if self.cache_path.exists():
                data = json.loads(self.cache_path.read_text(encoding="utf-8"))
                self._entries = data.get("entries", [])
        except Exception:
            self._entries = []

    def _persist(self) -> None:
        """写回磁盘（vec 不持久化，加载时重算——体积小且模型稳定）"""
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            slim = [{
                "jd_text": e["jd_text"],
                "analysis": e["analysis"],
                "ts": e["ts"],
            } for e in self._entries]
            self.cache_path.write_text(
                json.dumps({"entries": slim}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass  # 缓存写失败不影响主流程

    # ── 检索 ────────────────────────────────────────────

    def lookup(self, jd_text: str) -> JDAnalysis | None:
        """
        语义检索: 与历史 JD 算相似度，≥ 阈值返回缓存结果。

        嵌入不可用（模型加载失败）→ 返回 None（缓存降级关闭，正常解析）。
        """
        vec = self._embedder.embed(jd_text)
        if vec is None:
            return None

        best_sim = 0.0
        best_entry = None
        for e in self._entries:
            e_vec = e.get("vec")
            if e_vec is None:
                e_vec = self._embedder.embed(e["jd_text"])
                e["vec"] = e_vec
                if e_vec is None:
                    continue
            sim = _cosine(vec, e_vec)
            if sim > best_sim:
                best_sim = sim
                best_entry = e

        if best_entry is not None and best_sim >= self.threshold:
            return _dict_to_jd(best_entry["analysis"])
        return None

    def store(self, jd_text: str, analysis: JDAnalysis) -> None:
        """写入缓存（同文本更新，否则追加；超容量 FIFO 淘汰）"""
        vec = self._embedder.embed(jd_text)
        entry = {
            "jd_text": jd_text,
            "analysis": _jd_to_dict(analysis),
            "ts": time.time(),
            "vec": vec,
        }
        # 同文本替换
        for i, e in enumerate(self._entries):
            if e["jd_text"] == jd_text:
                self._entries[i] = entry
                self._persist()
                return
        self._entries.append(entry)
        # FIFO 淘汰最旧
        if len(self._entries) > self.max_entries:
            self._entries.sort(key=lambda e: e.get("ts", 0))
            self._entries = self._entries[-self.max_entries:]
        self._persist()

    def clear(self) -> None:
        """清空缓存（测试/管理用）"""
        self._entries = []
        self._persist()

    # ── 可观测性 ────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "entries": len(self._entries),
            "threshold": self.threshold,
            "cache_path": str(self.cache_path),
            "embedder_available": self._embedder._model is not None,
        }
