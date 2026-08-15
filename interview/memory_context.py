"""
面试记忆上下文 (Memory Context)
================================
把记忆模块真正接进面试链路：

1. 轮内记忆 (History Summary) — 确定性、零依赖
   把前几轮 Q&A 的要点压缩成摘要，注入评估 prompt。
   效果: 面试官能"翻旧账"（"你刚才说用了 Kafka，为什么现在又说用 MQ？"），
   追问决策不再只看当前题。

2. 跨会话记忆 (InterviewMemory) — ChromaDB 向量检索
   每道题结束后把 (题目/回答/评分/技能标签) 写入向量库；
   新面试开始时语义检索历史弱项，注入追问策略提示。

降级设计:
   ChromaDB/Sentence-Transformers 未安装时自动降级为进程内 dict 存储
   （只存元数据不存向量），功能可用但不跨进程持久化 — CI 和精简环境不崩。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from utils.logger import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════
#  轮内记忆: 历史摘要（确定性，零依赖，零 API 调用）
# ═══════════════════════════════════════════════════════════════

def build_history_summary(
    answers: list[dict],
    max_items: int = 5,
    max_len: int = 600,
) -> str:
    """
    把已回答的题目压缩成确定性摘要（不调 LLM）。

    只保留对追问决策有用的信息: 类别、得分、弱项。
    最近一轮排最前（对当前追问影响最大）。

    Args:
        answers: interviewer.state.answers 格式 [{question, answer, evaluation, is_follow_up}]
        max_items: 最多保留多少轮
        max_len: 摘要最大长度（token 预算保护）

    Returns:
        摘要文本，如:
        "第1题(数据库) 6.5分, 弱点: 索引原理不深入; 第2题(系统设计) 8.0分, 无弱点"
    """
    if not answers:
        return ""

    lines = []
    for i, record in enumerate(reversed(answers), start=1):
        if i > max_items:
            break

        q = record.get("question")
        ev = record.get("evaluation")
        category = getattr(q, "category", "") or "综合"
        is_follow_up = record.get("is_follow_up", False)

        if ev is None:
            lines.append(f"第{i}题({category}) 跳过")
            continue

        parts = [f"第{i}题({category}) {ev.total_score}分"]
        if is_follow_up:
            parts.append("(追问轮)")
        if ev.weaknesses:
            parts.append(f"弱点: {', '.join(ev.weaknesses[:2])}")
        if ev.matched_points:
            parts.append(f"覆盖: {', '.join(ev.matched_points[:3])}")
        lines.append(" | ".join(parts))

    summary = "; ".join(lines)
    return summary[:max_len]


# ═══════════════════════════════════════════════════════════════
#  跨会话记忆: 向量存储 + 优雅降级
# ═══════════════════════════════════════════════════════════════

@dataclass
class MemoryEntry:
    """一条面试记忆"""
    question: str
    answer: str
    score: float
    category: str = ""
    question_type: str = ""
    skills: list[str] = field(default_factory=list)
    session_id: str = ""


class InterviewMemory:
    """
    跨会话面试记忆。

    存储每道题的 Q/A/评分；检索历史弱项。

    降级策略（三级）:
        1. ChromaDB 可用 → 向量语义检索（跨会话持久化）
        2. ChromaDB 不可用 → 进程内 dict + 关键词检索（会话内有效）
        3. 任何异常 → no-op（记忆功能关闭，面试不中断）
    """

    def __init__(self, persist_dir: str | None = None, use_chroma: bool = True):
        self._entries: list[MemoryEntry] = []  # 进程内兜底存储
        self._chroma = None                    # ChromaDB 客户端（懒初始化）
        self._persist_dir = persist_dir
        self._backend = "none"                 # "chroma" | "memory" | "none"
        # 是否启用 ChromaDB 后端（离线工具/benchmark 传 False 用纯内存，
        # 避免初始化 chroma + 加载 embedding 模型触发网络）
        self._use_chroma = use_chroma

    # ── 初始化 ChromaDB（懒加载 + 失败降级）──────────────

    def _ensure_chroma(self) -> bool:
        if self._chroma is not None:
            return True
        if not self._use_chroma:
            self._backend = "memory"
            return False
        try:
            from memory.vector_store import VectorMemory

            kwargs = {"collection_name": "interview_history"}
            if self._persist_dir:
                kwargs["persist_dir"] = self._persist_dir
            self._chroma = VectorMemory(**kwargs)
            self._backend = "chroma"
            logger.info("面试记忆后端: ChromaDB 向量存储")
            return True
        except ImportError as e:
            logger.warning(f"ChromaDB 未安装，记忆降级为进程内存储: {e}")
            self._backend = "memory"
        except Exception as e:
            logger.warning(f"ChromaDB 初始化失败，记忆降级为进程内存储: {e}")
            self._backend = "memory"
        return False

    # ── 写入 ────────────────────────────────────────────

    def remember_answer(self, entry: MemoryEntry) -> None:
        """
        记录一道题的作答结果（同步、容错、不阻塞面试流程）。
        """
        self._entries.append(entry)

        if not self._ensure_chroma():
            return
        try:
            content = (
                f"题目: {entry.question[:200]}\n"
                f"回答: {entry.answer[:300]}\n"
                f"得分: {entry.score}/10"
            )
            self._chroma.remember(
                content,
                metadata={
                    "score": entry.score,
                    "category": entry.category,
                    "question_type": entry.question_type,
                    "session_id": entry.session_id,
                    "skills": json.dumps(entry.skills, ensure_ascii=False),
                },
            )
        except Exception as e:
            logger.warning(f"记忆写入失败（不影响面试）: {e}")

    # ── 检索 ────────────────────────────────────────────

    def recall_weaknesses(
        self,
        skills: list[str],
        score_threshold: float = 7.0,
        top_k: int = 3,
    ) -> list[str]:
        """
        检索历史弱项 — 与当前 JD 技能相关且得分偏低的题目。

        Args:
            skills: 当前 JD 的技能列表（语义检索查询）
            score_threshold: 得分低于此值视为弱项
            top_k: 最多返回条数

        Returns:
            弱项描述列表，如 ["数据库类题目得分 5.5，索引原理不深入", ...]
        """
        weak_entries: list[MemoryEntry] = []

        if self._ensure_chroma():
            try:
                query = " ".join(skills[:10]) or "面试"
                results = self._chroma.recall(query, top_k=top_k * 2)
                for r in results:
                    meta = r.get("metadata", {})
                    try:
                        score = float(meta.get("score", 10))
                    except (TypeError, ValueError):
                        score = 10
                    if score < score_threshold:
                        # 从文档内容中还原一条弱项描述
                        content = r.get("content", "")
                        weak_entries.append(self._content_to_entry(content, score, meta))
                        if len(weak_entries) >= top_k:
                            break
            except Exception as e:
                logger.warning(f"向量检索失败，回退进程内检索: {e}")

        # 进程内兜底（chroma 不可用或检索为空时补充）
        if len(weak_entries) < top_k:
            weak_entries = [
                e for e in self._entries
                if e.score < score_threshold
            ][-top_k:]

        return [self._format_weakness(e) for e in weak_entries[:top_k]]

    # ── 工具 ────────────────────────────────────────────

    def _content_to_entry(self, content: str, score: float, meta: dict) -> MemoryEntry:
        """从向量库返回的文档内容还原 MemoryEntry（尽力而为）"""
        question = ""
        for line in content.split("\n"):
            if line.startswith("题目: "):
                question = line[4:]
                break
        return MemoryEntry(
            question=question,
            answer="",
            score=score,
            category=meta.get("category", ""),
            question_type=meta.get("question_type", ""),
        )

    def _format_weakness(self, entry: MemoryEntry) -> str:
        label = entry.category or entry.question_type or "综合"
        return f"{label}类题目历史得分 {entry.score:.1f}，需重点考察"

    @property
    def backend(self) -> str:
        """当前记忆后端（调试/观测用）"""
        return self._backend

    @property
    def entry_count(self) -> int:
        return len(self._entries)


# ── 便捷函数 ────────────────────────────────────────────────────

def remember_answer_async(memory: InterviewMemory | None, entry: MemoryEntry) -> None:
    """
    异步安全的记忆写入 — 放到事件循环线程池，不阻塞面试主流程。
    写入失败静默（记忆是增强功能，不是核心链路）。
    """
    if memory is None:
        return
    try:
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, memory.remember_answer, entry)
    except RuntimeError:
        memory.remember_answer(entry)
