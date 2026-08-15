"""
全局用量统计测试（B1 Web 统计页）
=================================
覆盖 web/server.py 的 _aggregate_stats:
    - 空数据 → 全 0
    - 已完成会话聚合 token/成本/延迟
    - per_session 明细结构正确
    - 非完成/无 metrics 会话跳过

全离线（构造 SessionMeta 数据，不依赖网络），CI 可直接运行。
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from interview.session_manager import SessionMeta  # noqa: E402
from web.server import _aggregate_stats  # noqa: E402


def _meta(sid: str, position: str, status: str = "completed",
          score: float = 7.0, qcount: int = 2) -> SessionMeta:
    return SessionMeta(
        session_id=sid,
        position=position,
        created_at=datetime.now().isoformat(),
        status=status,
        overall_score=score,
        question_count=qcount,
    )


class TestAggregateStats:

    def test_empty(self, monkeypatch):
        """无会话 → 全 0"""
        monkeypatch.setattr("web.server.session_mgr", _FakeMgr([]))
        s = _aggregate_stats([])
        assert s["completed_sessions"] == 0
        assert s["total_tokens"] == 0
        assert s["per_session"] == []

    def test_single_completed_session(self, monkeypatch):
        """单个已完成会话: token/成本/延迟按 metrics 聚合"""
        metrics = {
            "evaluate": {
                "prompt_tokens": 1000, "completion_tokens": 500,
                "latency": 3.5, "model": "deepseek-v4-flash",
            },
            "report": {
                "prompt_tokens": 2000, "completion_tokens": 1000,
                "latency": 5.0, "model": "deepseek-v4-pro",
            },
        }
        mgr = _FakeMgr([_meta("s1", "Python 后端")], {"s1": metrics})
        monkeypatch.setattr("web.server.session_mgr", mgr)
        s = _aggregate_stats(mgr.list_sessions())

        assert s["completed_sessions"] == 1
        assert s["total_prompt_tokens"] == 3000
        assert s["total_completion_tokens"] == 1500
        assert s["total_tokens"] == 4500
        assert s["total_latency_sec"] == 8.5
        # 成本: flash 1000*0.2/1e6 + 500*1.0/1e6 + pro 2000*1.0/1e6 + 1000*4.0/1e6
        #      = 0.0002 + 0.0005 + 0.002 + 0.004 = 0.0067
        assert abs(s["estimated_cost_yuan"] - 0.0067) < 1e-6

        # per_session 明细
        assert len(s["per_session"]) == 1
        p = s["per_session"][0]
        assert p["session_id"] == "s1"
        assert p["position"] == "Python 后端"
        assert p["total_tokens"] == 4500
        assert p["cost_yuan"] == 0.0067
        assert p["overall_score"] == 7.0
        assert p["question_count"] == 2

    def test_skips_incomplete_and_no_metrics(self, monkeypatch):
        """非完成状态 / 无 metrics 的会话跳过"""
        metrics = {
            "evaluate": {"prompt_tokens": 100, "completion_tokens": 50,
                         "latency": 1.0, "model": "deepseek-v4-flash"},
        }
        mgr = _FakeMgr(
            [
                _meta("s1", "完成"),                 # completed + metrics → 计入
                _meta("s2", "进行中", status="in_progress"),  # 未完成 → 跳过
                _meta("s3", "无指标", status="completed"),    # 无 metrics → 跳过
            ],
            {"s1": metrics, "s2": metrics, "s3": None},
        )
        monkeypatch.setattr("web.server.session_mgr", mgr)
        s = _aggregate_stats(mgr.list_sessions())
        assert s["completed_sessions"] == 1
        assert len(s["per_session"]) == 1
        assert s["per_session"][0]["session_id"] == "s1"


# ── Fake session manager（避免真实磁盘 IO）─────────────────────

class _FakeMgr:
    """模拟 SessionManager: list_sessions 返回 metas, load 返回带 metrics 的 record"""

    def __init__(self, metas: list[SessionMeta], metrics_map: dict | None = None):
        self._metas = metas
        self._metrics = metrics_map or {}

    def list_sessions(self, limit=100):
        return self._metas[:limit]

    def load(self, session_id: str):
        metrics = self._metrics.get(session_id)
        if metrics is None:
            return None
        return type("R", (), {
            "interviewer_state": {"state": {"metrics": metrics}},
        })()
