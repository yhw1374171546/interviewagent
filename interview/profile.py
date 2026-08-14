"""
能力画像 (Ability Profile)
==========================
跨会话聚合每个技能分类的表现，形成能力画像（强弱项 / 进步趋势）。

数据来源: SessionManager 里所有会话的 interviewer_state.state.answers，
按 question.category 聚合 evaluation 的 4 维得分。

这是记忆能力的「可视化出口」——把零散的历史答题记录聚合成
「我哪些方向强、哪些方向弱、有没有进步」的结构化画像。

为什么不用向量检索（InterviewMemory）: 向量检索解决「语义相似」，
能力画像解决「结构化统计」，两者互补——画像直接读磁盘 JSON 即可，零 LLM 依赖。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field


def score_from_ev(ev: dict) -> float:
    """从序列化的 evaluation dict 算总分（与 EvaluationResult.total_score 同口径）。

    注意: total_score 是 property，dataclasses.asdict 不会序列化它，
    必须用 4 维加权重算。
    """
    return round(
        ev.get("correctness", 0) * 0.35
        + ev.get("depth", 0) * 0.25
        + ev.get("structure", 0) * 0.20
        + ev.get("relevance", 0) * 0.20,
        1,
    )


@dataclass
class SkillStat:
    """单个技能分类的统计"""
    category: str
    attempts: int = 0
    avg_score: float = 0.0
    avg_correctness: float = 0.0
    avg_depth: float = 0.0
    avg_structure: float = 0.0
    avg_relevance: float = 0.0
    first_score: float = 0.0   # 最早一次得分（进步趋势起点）
    last_score: float = 0.0    # 最近一次得分

    @property
    def trend(self) -> float:
        """进步幅度（正=提升，负=退步）"""
        return round(self.last_score - self.first_score, 1)


@dataclass
class AbilityProfile:
    """能力画像"""
    skills: list[SkillStat] = field(default_factory=list)   # 按平均分升序（弱项在前）
    weakest: list[str] = field(default_factory=list)
    strongest: list[str] = field(default_factory=list)
    total_sessions: int = 0
    total_attempts: int = 0

    def to_dict(self) -> dict:
        return {
            "total_sessions": self.total_sessions,
            "total_attempts": self.total_attempts,
            "weakest": self.weakest,
            "strongest": self.strongest,
            "skills": [
                {
                    "category": s.category,
                    "attempts": s.attempts,
                    "avg_score": s.avg_score,
                    "avg_correctness": s.avg_correctness,
                    "avg_depth": s.avg_depth,
                    "avg_structure": s.avg_structure,
                    "avg_relevance": s.avg_relevance,
                    "first_score": s.first_score,
                    "last_score": s.last_score,
                    "trend": s.trend,
                }
                for s in self.skills
            ],
        }


class ProfileBuilder:
    """跨会话能力画像聚合器"""

    def __init__(self, session_manager):
        self.mgr = session_manager

    def build(self, min_attempts: int = 1, weakest_count: int = 3) -> AbilityProfile:
        """
        聚合所有会话的答题记录，生成能力画像。

        Args:
            min_attempts: 至少答过多少次才计入画像（过滤噪音）
            weakest_count: 弱项/强项各取多少个
        """
        sessions = self.mgr.list_sessions(limit=500)
        sessions.sort(key=lambda m: m.created_at)  # 时间正序，用于进步趋势

        per_skill: dict[str, dict] = defaultdict(lambda: {"scores": [], "evals": []})
        total_attempts = 0
        session_count = 0

        for meta in sessions:
            record = self.mgr.load(meta.session_id)
            if not record:
                continue
            state = (record.interviewer_state or {}).get("state", {})
            answers = state.get("answers", [])
            if not answers:
                continue
            session_count += 1
            for a in answers:
                q = a.get("question") or {}
                ev = a.get("evaluation")
                if ev is None:
                    continue
                category = q.get("category") or "综合"
                per_skill[category]["scores"].append(score_from_ev(ev))
                per_skill[category]["evals"].append(ev)
                total_attempts += 1

        skills = []
        for category, data in per_skill.items():
            scores = data["scores"]
            evals = data["evals"]
            if len(scores) < min_attempts:
                continue
            n = len(scores)
            skills.append(SkillStat(
                category=category,
                attempts=n,
                avg_score=round(sum(scores) / n, 1),
                avg_correctness=round(sum(e.get("correctness", 0) for e in evals) / n, 1),
                avg_depth=round(sum(e.get("depth", 0) for e in evals) / n, 1),
                avg_structure=round(sum(e.get("structure", 0) for e in evals) / n, 1),
                avg_relevance=round(sum(e.get("relevance", 0) for e in evals) / n, 1),
                first_score=scores[0],
                last_score=scores[-1],
            ))

        skills.sort(key=lambda s: s.avg_score)  # 弱项在前

        return AbilityProfile(
            skills=skills,
            weakest=[s.category for s in skills[:weakest_count]],
            strongest=[s.category for s in skills[::-1][:weakest_count]],
            total_sessions=session_count,
            total_attempts=total_attempts,
        )
