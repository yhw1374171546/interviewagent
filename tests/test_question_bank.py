"""
题库与检索测试（阶段 3）
======================
覆盖倒排索引、标签打分、分层配额、去重、通识补齐、排序。
全部离线（纯数据），CI 可直接运行。
"""

from interview.question_bank import (
    QUESTION_BANK,
    BankQuestion,
    QuestionBankRetriever,
    QuestionType,
)


class TestBuildIndex:

    def test_tag_index_built(self):
        r = QuestionBankRetriever()
        assert "python" in r._tag_index
        assert any(q.id == "PY001" for q in r._tag_index["python"])

    def test_tag_index_case_insensitive(self):
        r = QuestionBankRetriever()
        # 索引按小写 tag 建
        assert all(t == t.lower() for t in r._tag_index)


class TestRetrieve:

    def test_returns_requested_count(self):
        r = QuestionBankRetriever()
        qs = r.retrieve(["python", "mysql", "redis"], total=8)
        assert len(qs) == 8

    def test_no_duplicate_ids(self):
        r = QuestionBankRetriever()
        qs = r.retrieve(["python", "mysql", "redis", "kafka", "go"], total=8)
        ids = [q.id for q in qs]
        assert len(ids) == len(set(ids))

    def test_exact_match_ranked_first(self):
        r = QuestionBankRetriever()
        qs = r.retrieve(["python"], total=3)
        # python 精确命中的题应排在最前
        assert qs[0].id.startswith("PY")

    def test_exclude_ids(self):
        r = QuestionBankRetriever()
        qs = r.retrieve(["python"], total=5, exclude_ids={"PY001", "PY002"})
        assert all(q.id not in {"PY001", "PY002"} for q in qs)

    def test_empty_skills_still_returns_generic(self):
        r = QuestionBankRetriever()
        qs = r.retrieve([], total=3)
        assert len(qs) == 3

    def test_coding_question_guaranteed_even_without_match(self):
        """强制代码题: 检索不到 coding 匹配时也从题库补 1 道（用户硬需求）"""
        r = QuestionBankRetriever()
        # 空技能 + 高分通用题 → 即使检索全不中 coding，结果也必须含代码题
        for skills in ([], ["未知技能xyz"], ["python", "mysql"]):
            qs = r.retrieve(skills, total=4)
            assert any(q.type == QuestionType.CODING for q in qs), skills

    def test_coding_guarantee_does_not_break_total(self):
        r = QuestionBankRetriever()
        qs = r.retrieve([], total=5)
        assert len(qs) == 5
        assert any(q.type == QuestionType.CODING for q in qs)


class TestStratifiedSelect:

    def test_covers_all_five_types(self):
        r = QuestionBankRetriever()
        ranked = [(q, 5) for q in QUESTION_BANK]
        selected = r._stratified_select(ranked, 8)
        types = {q.type for q in selected}
        assert types == {
            QuestionType.TECHNICAL,
            QuestionType.SCENARIO,
            QuestionType.PROJECT,
            QuestionType.BEHAVIORAL,
            QuestionType.CODING,
        }

    def test_technical_gets_larger_quota(self):
        r = QuestionBankRetriever()
        ranked = [(q, 5) for q in QUESTION_BANK]
        selected = r._stratified_select(ranked, 8)
        technical = sum(1 for q in selected if q.type == QuestionType.TECHNICAL)
        assert technical >= 2  # 8 * 0.35 = 2


class TestFillGeneric:

    def test_fill_counts_and_excludes(self):
        r = QuestionBankRetriever()
        filled = r._fill_generic(3, {"PY001", "PY002"})
        assert len(filled) == 3
        assert all(q.id not in {"PY001", "PY002"} for q in filled)


class TestGetByIdAndStats:

    def test_get_by_id(self):
        r = QuestionBankRetriever()
        assert r.get_by_id("PY001").category == "Python基础"
        assert r.get_by_id("NOPE") is None

    def test_stats(self):
        r = QuestionBankRetriever()
        s = r.stats()
        assert s["total_questions"] == len(QUESTION_BANK)
        assert s["by_type"]["technical"] > 0
        assert s["unique_tags"] > 0


class TestDataclasses:

    def test_bank_question_defaults(self):
        q = BankQuestion(id="X", type=QuestionType.TECHNICAL, category="c", question="q", tags=["t"])
        assert q.difficulty == 3
        assert q.expected_points == []
