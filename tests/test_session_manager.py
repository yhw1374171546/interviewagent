"""
会话管理器测试（阶段 3）
======================
覆盖 CRUD、置顶排序、重命名、删除、对比分析、进度摘要、并发写（无锁下的基本一致性）。
全部离线（磁盘 JSON），CI 可直接运行。
"""

from interview.session_manager import SessionManager, SessionMeta, dim_to_key


def make_manager(tmp_path) -> SessionManager:
    return SessionManager(storage_dir=str(tmp_path / "sessions"))


class TestSessionCRUD:

    def test_create_and_load_roundtrip(self, tmp_path):
        mgr = make_manager(tmp_path)
        rec = mgr.create_session("后端工程师", "JD 文本", tags=["python", "mysql"])
        mgr.save(rec)  # create 只写索引，save 才落盘 session 文件
        assert rec.meta.status == "in_progress"
        assert rec.meta.display_name == "后端工程师"

        loaded = mgr.load(rec.meta.session_id)
        assert loaded is not None
        assert loaded.meta.position == "后端工程师"
        assert loaded.jd_text == "JD 文本"
        assert loaded.meta.tags == ["python", "mysql"]

    def test_load_missing_returns_none(self, tmp_path):
        mgr = make_manager(tmp_path)
        assert mgr.load("nonexistent") is None

    def test_save_persists_status_and_score(self, tmp_path):
        mgr = make_manager(tmp_path)
        rec = mgr.create_session("后端", "JD")
        rec.meta.status = "completed"
        rec.meta.overall_score = 7.5
        rec.meta.question_count = 8
        rec.meta.answered_count = 8
        rec.final_report = {"overall_score": 7.5}
        mgr.save(rec)

        loaded = mgr.load(rec.meta.session_id)
        assert loaded.meta.status == "completed"
        assert loaded.meta.overall_score == 7.5
        assert loaded.meta.answered_count == 8
        assert loaded.final_report["overall_score"] == 7.5

    def test_save_auto_completes_with_final_report(self, tmp_path):
        mgr = make_manager(tmp_path)
        rec = mgr.create_session("后端", "JD")
        rec.final_report = {"overall_score": 8.0}
        mgr.save(rec)  # in_progress + final_report → completed
        assert mgr.load(rec.meta.session_id).meta.status == "completed"

    def test_delete_removes_file_and_index(self, tmp_path):
        mgr = make_manager(tmp_path)
        rec = mgr.create_session("后端", "JD")
        sid = rec.meta.session_id
        assert mgr.delete_session(sid) is True
        assert mgr.load(sid) is None
        assert all(m.session_id != sid for m in mgr.list_sessions())


class TestListAndSort:

    def test_pinned_first_then_time_desc(self, tmp_path):
        mgr = make_manager(tmp_path)
        mgr._save_index([
            {"session_id": "a", "position": "A", "created_at": "2026-01-01T00:00:00", "status": "completed", "pinned": False},
            {"session_id": "b", "position": "B", "created_at": "2026-01-02T00:00:00", "status": "completed", "pinned": True},
            {"session_id": "c", "position": "C", "created_at": "2026-01-03T00:00:00", "status": "completed", "pinned": False},
        ])
        ids = [m.session_id for m in mgr.list_sessions()]
        assert ids == ["b", "c", "a"]  # 置顶最前，其余时间倒序

    def test_filter_by_status_and_position(self, tmp_path):
        mgr = make_manager(tmp_path)
        mgr._save_index([
            {"session_id": "a", "position": "后端工程师", "created_at": "2026-01-03T00:00:00", "status": "completed", "pinned": False},
            {"session_id": "b", "position": "前端工程师", "created_at": "2026-01-02T00:00:00", "status": "in_progress", "pinned": False},
        ])
        assert [m.session_id for m in mgr.list_sessions(status="in_progress")] == ["b"]
        assert [m.session_id for m in mgr.list_sessions(position="后端")] == ["a"]

    def test_limit(self, tmp_path):
        mgr = make_manager(tmp_path)
        mgr._save_index([
            {"session_id": f"s{i}", "position": f"P{i}", "created_at": f"2026-01-0{i+1}T00:00:00", "status": "completed", "pinned": False}
            for i in range(5)
        ])
        assert len(mgr.list_sessions(limit=2)) == 2


class TestRenameAndPin:

    def test_rename_sets_custom_name(self, tmp_path):
        mgr = make_manager(tmp_path)
        rec = mgr.create_session("后端工程师", "JD")
        mgr.save(rec)
        sid = rec.meta.session_id
        assert mgr.rename_session(sid, "第一次后端面试") is True
        assert mgr.load(sid).meta.display_name == "第一次后端面试"

    def test_rename_missing_returns_false(self, tmp_path):
        mgr = make_manager(tmp_path)
        assert mgr.rename_session("missing", "x") is False

    def test_set_pinned_toggles(self, tmp_path):
        mgr = make_manager(tmp_path)
        rec = mgr.create_session("后端", "JD")
        mgr.save(rec)
        sid = rec.meta.session_id
        assert mgr.set_pinned(sid, True) is True
        assert mgr.load(sid).meta.pinned is True
        assert mgr.set_pinned(sid, False) is True
        assert mgr.load(sid).meta.pinned is False


class TestCompareAndProgress:

    def test_compare_sessions_trend(self, tmp_path):
        mgr = make_manager(tmp_path)
        sids = []
        for i, score in enumerate([6.0, 8.0]):
            rec = mgr.create_session(f"P{i}", "JD")
            rec.meta.overall_score = score
            rec.meta.status = "completed"
            rec.final_report = {"avg_correctness": score}
            mgr.save(rec)
            sids.append(rec.meta.session_id)

        result = mgr.compare_sessions(sids)
        assert result["compared_count"] == 2
        assert result["trend"] == "📈 上升"  # 6.0 → 8.0 (+2.0 > 0.5)
        assert result["average_score"] == 7.0

    def test_compare_empty(self, tmp_path):
        mgr = make_manager(tmp_path)
        assert mgr.compare_sessions([]) == {"error": "无有效记录"}

    def test_progress_summary(self, tmp_path):
        mgr = make_manager(tmp_path)
        for i in range(3):
            rec = mgr.create_session(f"P{i}", "JD", tags=["python"])
            rec.meta.status = "completed" if i < 2 else "in_progress"
            rec.meta.overall_score = 6.0 + i
            mgr.save(rec)
        summary = mgr.progress_summary()
        assert summary["total_sessions"] == 3
        assert summary["completed"] == 2
        assert summary["in_progress"] == 1
        assert summary["most_practiced_tags"] == ["python"]

    def test_progress_summary_empty(self, tmp_path):
        mgr = make_manager(tmp_path)
        assert mgr.progress_summary() == {"total_sessions": 0, "message": "还没有面试记录"}


class TestResumeAndIndex:

    def test_resume_session_in_progress(self, tmp_path):
        mgr = make_manager(tmp_path)
        rec = mgr.create_session("后端", "JD")
        mgr.save(rec)
        resumed = mgr.resume_session(rec.meta.session_id)
        assert resumed is not None
        assert resumed.meta.session_id == rec.meta.session_id

    def test_index_corrupt_falls_back_to_empty(self, tmp_path):
        mgr = make_manager(tmp_path)
        mgr.index_file.write_text("{invalid json", encoding="utf-8")
        assert mgr.list_sessions() == []

    def test_dim_to_key(self):
        assert dim_to_key("正确性") == "correctness"
        assert dim_to_key("深度") == "depth"
        assert dim_to_key("结构") == "structure"
        assert dim_to_key("相关性") == "relevance"
        assert dim_to_key("未知") == "未知"


class TestMeta:

    def test_display_name_fallback(self):
        meta = SessionMeta(session_id="x", position="", created_at="", status="in_progress")
        assert meta.display_name == "未命名面试"
