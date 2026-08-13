"""
面试会话管理器
==============
管理多个面试会话的完整生命周期：创建、保存、加载、恢复、对比。

解决的业务问题:
    - 多轮、多会话: 用户可能针对不同 JD 进行多次模拟面试
    - 会话持久化: 面试中断后可以恢复继续
    - 跨会话分析: "我面了 5 次后端岗，薄弱环节在哪？"
    - 面试记录对比: 对比不同场次的表现变化

设计:
    - 每场面试对应一个 Session ID
    - Session 数据序列化为 JSON，持久化到磁盘
    - 元数据索引 — 快速查询历史会话
    - ChromaDB 存储向量 — 跨会话的语义检索
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)


# ── 数据结构 ───────────────────────────────────────────────────

@dataclass
class SessionMeta:
    """会话元数据 — 轻量，用于列表展示"""
    session_id: str
    position: str           # 岗位
    created_at: str          # ISO 时间
    status: str              # "in_progress" | "completed" | "abandoned"
    overall_score: float = 0.0
    question_count: int = 0
    answered_count: int = 0
    tags: list[str] = field(default_factory=list)  # JD 技能标签
    # Web 侧边栏功能
    pinned: bool = False     # 置顶
    custom_name: str = ""    # 用户重命名（优先于 position 展示）

    @property
    def display_name(self) -> str:
        """侧边栏展示名: 自定义名 > 岗位名"""
        return self.custom_name or self.position or "未命名面试"


@dataclass
class SessionRecord:
    """完整会话记录"""
    meta: SessionMeta
    jd_text: str = ""
    jd_analysis: dict = field(default_factory=dict)
    questions: list[dict] = field(default_factory=list)
    answers: list[dict] = field(default_factory=list)
    final_report: dict = field(default_factory=dict)
    # Web 聊天界面
    messages: list[dict] = field(default_factory=list)          # 聊天记录（UI 渲染用）
    interviewer_state: dict = field(default_factory=dict)       # Interviewer 状态快照（断点恢复用）

    def to_dict(self) -> dict:
        return {
            "meta": {
                "session_id": self.meta.session_id,
                "position": self.meta.position,
                "created_at": self.meta.created_at,
                "status": self.meta.status,
                "overall_score": self.meta.overall_score,
                "question_count": self.meta.question_count,
                "answered_count": self.meta.answered_count,
                "tags": self.meta.tags,
                "pinned": self.meta.pinned,
                "custom_name": self.meta.custom_name,
            },
            "jd_text": self.jd_text,
            "jd_analysis": self.jd_analysis,
            "questions": self.questions,
            "answers": self.answers,
            "final_report": self.final_report,
            "messages": self.messages,
            "interviewer_state": self.interviewer_state,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SessionRecord:
        meta_data = data.get("meta", {})
        meta = SessionMeta(
            session_id=meta_data.get("session_id", ""),
            position=meta_data.get("position", ""),
            created_at=meta_data.get("created_at", ""),
            status=meta_data.get("status", "in_progress"),
            overall_score=meta_data.get("overall_score", 0.0),
            question_count=meta_data.get("question_count", 0),
            answered_count=meta_data.get("answered_count", 0),
            tags=meta_data.get("tags", []),
            pinned=meta_data.get("pinned", False),
            custom_name=meta_data.get("custom_name", ""),
        )
        return cls(
            meta=meta,
            jd_text=data.get("jd_text", ""),
            jd_analysis=data.get("jd_analysis", {}),
            questions=data.get("questions", []),
            answers=data.get("answers", []),
            final_report=data.get("final_report", {}),
            messages=data.get("messages", []),
            interviewer_state=data.get("interviewer_state", {}),
        )


# ── 会话管理器 ─────────────────────────────────────────────────

class SessionManager:
    """
    面试会话管理器。

    功能:
        - create_session(): 创建新面试会话
        - save(): 保存当前会话到磁盘 + 向量存储
        - load(): 加载历史会话
        - list_sessions(): 列出所有历史会话
        - delete_session(): 删除会话
        - compare_sessions(): 对比多场面试表现
        - resume_session(): 恢复未完成的面试

    存储层次:
        1. 磁盘 JSON — 完整会话数据 (sessions/ 目录)
        2. 索引文件 — 会话元数据索引 (sessions/index.json)
        3. ChromaDB — 向量化存储答案用于跨会话检索

    使用:
        manager = SessionManager()
        session = manager.create_session("高级后端工程师", jd_text)
        # ... 面试进行中 ...
        manager.save(session)
        # 下次打开:
        sessions = manager.list_sessions()
        session = manager.load(sessions[0].session_id)
    """

    def __init__(self, storage_dir: str = "./data/sessions"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.storage_dir / "index.json"
        self._ensure_index()

    # ── 索引管理 ──────────────────────────────────────────

    def _ensure_index(self) -> None:
        """确保索引文件存在"""
        if not self.index_file.exists():
            self.index_file.write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")

    def _load_index(self) -> list[dict]:
        """加载会话索引"""
        try:
            data = self.index_file.read_text(encoding="utf-8")
            return json.loads(data)
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _save_index(self, index: list[dict]) -> None:
        """保存会话索引"""
        self.index_file.write_text(
            json.dumps(index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _session_path(self, session_id: str) -> Path:
        """获取会话文件路径"""
        return self.storage_dir / f"{session_id}.json"

    # ── Public API ────────────────────────────────────────

    def create_session(
        self,
        position: str,
        jd_text: str,
        tags: list[str] | None = None,
    ) -> SessionRecord:
        """
        创建新的面试会话。

        Args:
            position: 岗位名称
            jd_text: 原始 JD 文本
            tags: JD 技能标签（从规则引擎提取）

        Returns:
            新的 SessionRecord
        """
        session_id = str(uuid.uuid4())[:12]  # 短 ID，用户友好
        meta = SessionMeta(
            session_id=session_id,
            position=position,
            created_at=datetime.now().isoformat(),
            status="in_progress",
            tags=tags or [],
        )

        record = SessionRecord(
            meta=meta,
            jd_text=jd_text,
        )

        # 写入索引
        index = self._load_index()
        index.append({
            "session_id": session_id,
            "position": position,
            "created_at": meta.created_at,
            "status": "in_progress",
            "overall_score": 0.0,
            "question_count": 0,
            "tags": tags or [],
        })
        self._save_index(index)

        logger.info(f"创建会话: {session_id} — {position}")
        return record

    def save(self, record: SessionRecord) -> None:
        """
        保存会话到磁盘。

        如果 ChromaDB 可用，同时向量化存储答案。
        """
        # 更新状态
        if record.meta.status == "in_progress" and record.final_report:
            record.meta.status = "completed"

        # 保存完整记录
        file_path = self._session_path(record.meta.session_id)
        file_path.write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 更新索引
        index = self._load_index()
        entry = {
            "session_id": record.meta.session_id,
            "position": record.meta.position,
            "created_at": record.meta.created_at,
            "status": record.meta.status,
            "overall_score": record.meta.overall_score,
            "question_count": record.meta.question_count,
            "answered_count": record.meta.answered_count,
            "tags": record.meta.tags,
            "pinned": record.meta.pinned,
            "custom_name": record.meta.custom_name,
        }
        for i, existing in enumerate(index):
            if existing["session_id"] == record.meta.session_id:
                index[i] = entry
                break
        else:
            index.append(entry)
        self._save_index(index)

        logger.info(f"保存会话: {record.meta.session_id} (状态: {record.meta.status})")

    def load(self, session_id: str) -> SessionRecord | None:
        """
        加载历史会话。

        Args:
            session_id: 会话 ID

        Returns:
            SessionRecord 或 None
        """
        file_path = self._session_path(session_id)
        if not file_path.exists():
            logger.warning(f"会话不存在: {session_id}")
            return None

        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            return SessionRecord.from_dict(data)
        except Exception as e:
            logger.error(f"加载会话失败 {session_id}: {e}")
            return None

    def list_sessions(
        self,
        status: str | None = None,
        position: str | None = None,
        limit: int = 20,
    ) -> list[SessionMeta]:
        """
        列出历史会话。

        排序规则: 置顶的在前，其余按创建时间倒序（最新在前）。

        Args:
            status: 按状态过滤 (in_progress/completed/abandoned)
            position: 按岗位名称模糊匹配
            limit: 最大返回数

        Returns:
            SessionMeta 列表
        """
        index = self._load_index()
        metas = []

        for entry in index:
            if status and entry.get("status") != status:
                continue
            if position and position.lower() not in entry.get("position", "").lower():
                continue
            metas.append(SessionMeta(
                session_id=entry.get("session_id", ""),
                position=entry.get("position", ""),
                created_at=entry.get("created_at", ""),
                status=entry.get("status", "unknown"),
                overall_score=entry.get("overall_score", 0.0),
                question_count=entry.get("question_count", 0),
                answered_count=entry.get("answered_count", 0),
                tags=entry.get("tags", []),
                pinned=entry.get("pinned", False),
                custom_name=entry.get("custom_name", ""),
            ))

        # 两步稳定排序: 先按时间倒序，再按置顶分组（组内保持时间倒序）
        metas.sort(key=lambda m: m.created_at, reverse=True)
        metas.sort(key=lambda m: m.pinned, reverse=True)
        return metas[:limit]

    def rename_session(self, session_id: str, new_name: str) -> bool:
        """
        重命名会话（自定义显示名）。

        Args:
            session_id: 会话 ID
            new_name: 新的显示名（空字符串则清除自定义名，回退到岗位名）

        Returns:
            是否成功
        """
        record = self.load(session_id)
        if not record:
            return False

        record.meta.custom_name = new_name.strip()
        self.save(record)
        logger.info(f"重命名会话 {session_id}: {record.meta.display_name}")
        return True

    def set_pinned(self, session_id: str, pinned: bool) -> bool:
        """
        置顶/取消置顶会话。

        Args:
            session_id: 会话 ID
            pinned: True 置顶 / False 取消置顶

        Returns:
            是否成功
        """
        record = self.load(session_id)
        if not record:
            return False

        record.meta.pinned = pinned
        self.save(record)
        logger.info(f"{'置顶' if pinned else '取消置顶'}会话 {session_id}")
        return True

    def delete_session(self, session_id: str) -> bool:
        """删除会话"""
        file_path = self._session_path(session_id)
        if file_path.exists():
            file_path.unlink()

        # 更新索引
        index = self._load_index()
        index = [e for e in index if e["session_id"] != session_id]
        self._save_index(index)

        logger.info(f"删除会话: {session_id}")
        return True

    def resume_session(self, session_id: str) -> SessionRecord | None:
        """
        恢复未完成的面试。

        返回的 SessionRecord 中 answers 为非空列表，
        可以通过 Interviewer 继续从上次中断处开始。
        """
        record = self.load(session_id)
        if not record:
            return None
        if record.meta.status not in ("in_progress",):
            logger.warning(f"会话 {session_id} 已完成，无需恢复")
        return record

    def compare_sessions(self, session_ids: list[str]) -> dict:
        """
        多场面试横向对比分析。

        Args:
            session_ids: 要对比的会话 ID 列表

        Returns:
            {
                "sessions": [...],
                "trend": "上升/下降/稳定",
                "best_dimension": "正确性/深度/结构/相关性",
                "worst_dimension": "...",
                "average_score": 7.5,
            }
        """
        records = []
        for sid in session_ids:
            r = self.load(sid)
            if r:
                records.append(r)

        if not records:
            return {"error": "无有效记录"}

        scores = [r.meta.overall_score for r in records if r.meta.overall_score > 0]
        avg_score = sum(scores) / len(scores) if scores else 0

        # 趋势分析
        if len(scores) >= 2:
            if scores[-1] > scores[0] + 0.5:
                trend = "📈 上升"
            elif scores[-1] < scores[0] - 0.5:
                trend = "📉 下降"
            else:
                trend = "➡️ 稳定"
        else:
            trend = "数据不足"

        # 维度分析
        dim_scores = {"正确性": [], "深度": [], "结构": [], "相关性": []}
        for r in records:
            report = r.final_report or {}
            for dim in dim_scores:
                val = report.get(f"avg_{dim_to_key(dim)}", 0)
                if val > 0:
                    dim_scores[dim].append(val)

        dim_avgs = {
            dim: sum(vs) / len(vs) if vs else 0
            for dim, vs in dim_scores.items()
        }
        best_dim = max(dim_avgs, key=dim_avgs.get) if dim_avgs else "—"
        worst_dim = min(dim_avgs, key=dim_avgs.get) if dim_avgs else "—"

        return {
            "compared_count": len(records),
            "trend": trend,
            "average_score": round(avg_score, 1),
            "best_dimension": best_dim,
            "worst_dimension": worst_dim,
            "dimension_scores": dim_avgs,
            "sessions": [
                {
                    "session_id": r.meta.session_id,
                    "position": r.meta.position,
                    "score": r.meta.overall_score,
                    "date": r.meta.created_at[:10],
                }
                for r in records
            ],
        }

    def progress_summary(self) -> dict:
        """
        学习进度摘要 — 适合放在首页 Dashboard。

        Returns:
            {
                "total_sessions": 总面试次数,
                "completed": 完成数,
                "in_progress": 进行中,
                "average_score": 平均分,
                "latest_score": 最近一次得分,
                "most_practiced_tags": [最多的技能标签],
                "improvement_areas": [待提升最多的维度],
            }
        """
        index = self._load_index()
        if not index:
            return {"total_sessions": 0, "message": "还没有面试记录"}

        completed = [e for e in index if e.get("status") == "completed"]
        scores = [e.get("overall_score", 0) for e in completed if e.get("overall_score", 0) > 0]

        # 统计最常练习的技能标签
        tag_counts: dict[str, int] = {}
        for e in index:
            for tag in e.get("tags", []):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        top_tags = sorted(tag_counts, key=tag_counts.get, reverse=True)[:5]

        return {
            "total_sessions": len(index),
            "completed": len(completed),
            "in_progress": sum(1 for e in index if e.get("status") == "in_progress"),
            "average_score": round(sum(scores) / len(scores), 1) if scores else 0,
            "latest_score": scores[-1] if scores else 0,
            "most_practiced_tags": top_tags,
            "improvement_areas": [],  # 需要跨会话分析
        }


def dim_to_key(dim: str) -> str:
    """中文维度名 → report 字段名"""
    mapping = {
        "正确性": "correctness",
        "深度": "depth",
        "结构": "structure",
        "相关性": "relevance",
    }
    return mapping.get(dim, dim)
