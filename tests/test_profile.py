"""
能力画像测试（阶段：记忆能力画像）
=================================
覆盖 score_from_ev 加权、跨会话按 category 聚合、强弱项、进步趋势。
全部离线（磁盘 JSON），CI 可直接运行。
"""

from interview.profile import ProfileBuilder, score_from_ev
from interview.session_manager import SessionManager


def _ev(correctness=8, depth=7, structure=7, relevance=8):
    return {
        "correctness": correctness, "depth": depth,
        "structure": structure, "relevance": relevance,
    }


def _make_session(mgr, position, created_at, answers):
    """创建一个带指定 answers 快照的会话"""
    rec = mgr.create_session(position, "JD")
    rec.meta.created_at = created_at
    rec.meta.status = "completed"
    rec.interviewer_state = {"state": {"answers": answers}}
    mgr.save(rec)
    return rec


class TestScoreFromEv:

    def test_weighted_score(self):
        # 8*0.35 + 7*0.25 + 7*0.20 + 8*0.20 = 7.55 → 7.6
        assert score_from_ev(_ev(8, 7, 7, 8)) == 7.6

    def test_missing_fields_default_zero(self):
        assert score_from_ev({}) == 0.0


class TestProfileBuilder:

    def test_empty_profile(self, tmp_path):
        mgr = SessionManager(storage_dir=str(tmp_path / "sessions"))
        profile = ProfileBuilder(mgr).build()
        assert profile.total_attempts == 0
        assert profile.skills == []

    def test_aggregate_by_category(self, tmp_path):
        mgr = SessionManager(storage_dir=str(tmp_path / "sessions"))
        _make_session(mgr, "后端", "2026-01-01T00:00:00", [
            {"question": {"category": "Python"}, "evaluation": _ev(8, 7, 7, 8)},
            {"question": {"category": "Python"}, "evaluation": _ev(9, 8, 8, 9)},
            {"question": {"category": "数据库"}, "evaluation": _ev(5, 5, 5, 5)},
        ])

        profile = ProfileBuilder(mgr).build()
        assert profile.total_attempts == 3
        assert profile.total_sessions == 1

        by_cat = {s.category: s for s in profile.skills}
        assert by_cat["Python"].attempts == 2
        assert by_cat["数据库"].attempts == 1
        # 数据库平均分低于 Python → 弱项
        assert by_cat["数据库"].avg_score < by_cat["Python"].avg_score

    def test_weakest_and_strongest(self, tmp_path):
        mgr = SessionManager(storage_dir=str(tmp_path / "sessions"))
        _make_session(mgr, "后端", "2026-01-01T00:00:00", [
            {"question": {"category": "A"}, "evaluation": _ev(3, 3, 3, 3)},
            {"question": {"category": "B"}, "evaluation": _ev(5, 5, 5, 5)},
            {"question": {"category": "C"}, "evaluation": _ev(7, 7, 7, 7)},
            {"question": {"category": "D"}, "evaluation": _ev(9, 9, 9, 9)},
        ])
        profile = ProfileBuilder(mgr).build()
        assert profile.weakest == ["A", "B", "C"]  # 平均分最低的前 3
        assert profile.strongest == ["D", "C", "B"]  # 平均分最高的前 3

    def test_trend_progress(self, tmp_path):
        mgr = SessionManager(storage_dir=str(tmp_path / "sessions"))
        # 两次会话，同一技能分数从低到高 → 进步
        _make_session(mgr, "P1", "2026-01-01T00:00:00", [
            {"question": {"category": "Redis"}, "evaluation": _ev(5, 5, 5, 5)},
        ])
        _make_session(mgr, "P2", "2026-02-01T00:00:00", [
            {"question": {"category": "Redis"}, "evaluation": _ev(8, 8, 8, 8)},
        ])
        profile = ProfileBuilder(mgr).build()
        redis = next(s for s in profile.skills if s.category == "Redis")
        assert redis.attempts == 2
        assert redis.first_score < redis.last_score
        assert redis.trend > 0

    def test_skips_no_evaluation(self, tmp_path):
        mgr = SessionManager(storage_dir=str(tmp_path / "sessions"))
        _make_session(mgr, "后端", "2026-01-01T00:00:00", [
            {"question": {"category": "Python"}, "evaluation": None},  # 跳过
            {"question": {"category": "Python"}, "evaluation": _ev(8, 8, 8, 8)},
        ])
        profile = ProfileBuilder(mgr).build()
        assert profile.total_attempts == 1
