"""
面经库 + RAG 检索测试（阶段：RAG 面经）
=====================================
覆盖轻量检索器的关键词/相似度打分、Top-K 截断、无相关兜底，
以及参考答案的 RAG 融合（面经替代 expected_points 兜底）。
全部离线（纯函数 + Mock），CI 可直接运行。
"""

import asyncio

from core.mock_llm import MockLLMClient
from interview.evaluator import EvaluationResult
from interview.jd_parser import JDAnalysis
from interview.qa_bank import (
    QaEntry,
    QaRetriever,
    get_qa_retriever,
)
from interview.question_bank import InterviewQuestion, QuestionType
from interview.report import ReportGenerator


def run(coro):
    return asyncio.run(coro)


# 旧暴力实现（对照基线：每次查询对全量重算 tokens/grams）
def _brute_force_retrieve(retriever: QaRetriever, query: str, top_k: int = 3) -> list[dict]:
    from interview.qa_bank import MIN_SCORE, _char_ngrams, _score_indexed, _tokenize

    q_tokens = _tokenize(query)
    q_grams = _char_ngrams(query)
    scored = []
    for e in retriever.entries:
        e_tokens = _tokenize(e.question) | {t.lower() for t in e.tags}
        e_grams = _char_ngrams(e.question) | _char_ngrams(" ".join(e.tags))
        score = _score_indexed(q_tokens, q_grams, e_tokens, e_grams)
        if score >= MIN_SCORE:
            scored.append((score, e))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {"id": e.id, "question": e.question, "answer": e.answer,
         "tags": e.tags, "score": round(s, 3)}
        for s, e in scored[:top_k]
    ]


class TestQaRetriever:

    def test_retrieve_gil_top1(self):
        r = QaRetriever()
        hits = r.retrieve("解释 Python 的 GIL 全局解释器锁", top_k=1)
        assert hits and hits[0]["id"] == "QA001"
        assert hits[0]["score"] > 0

    def test_retrieve_mysql_index(self):
        r = QaRetriever()
        hits = r.retrieve("MySQL 索引为什么用 B+ 树", top_k=2)
        assert hits and hits[0]["id"] == "QA002"

    def test_retrieve_irrelevant_empty(self):
        r = QaRetriever()
        hits = r.retrieve("zzzz qqqq xxyy", top_k=3)
        assert hits == []

    def test_top_k_respected(self):
        r = QaRetriever()
        hits = r.retrieve("Redis 为什么快 缓存", top_k=2)
        assert len(hits) <= 2

    def test_empty_query(self):
        r = QaRetriever()
        assert r.retrieve("") == []

    # ── C2 预计算索引 ─────────────────────────────────────

    def test_indexed_results_match_brute_force(self):
        """预计算索引检索结果与暴力实现完全一致（性能优化不改语义）"""
        r = QaRetriever()
        queries = [
            "解释 Python 的 GIL 全局解释器锁",
            "MySQL 索引为什么用 B+ 树",
            "Redis 为什么快 缓存",
            "TCP 三次握手和四次挥手",
            "zzzz qqqq xxyy",  # 无相关
            "docker 容器和镜像的区别",
        ]
        for q in queries:
            assert r.retrieve(q, top_k=3) == _brute_force_retrieve(r, q, top_k=3), q

    def test_index_built_lazily_and_reused(self):
        """索引惰性构建一次，之后查询复用（stats 可观测）"""
        r = QaRetriever()
        assert r.stats() == {"entries": len(r.entries), "indexed": False}
        r.retrieve("解释 Python 的 GIL")
        assert r.stats()["indexed"] is True

    def test_get_qa_retriever_shared_cache(self):
        """全量共享检索器进程级复用（索引只构建一次，跨报告不重建）"""
        a = get_qa_retriever()
        b = get_qa_retriever()
        assert a is b
        assert a.stats()["entries"] >= 500  # 内置 + knowledge + LC 全量数据源

    def test_custom_entries_do_not_leak_into_shared(self):
        """自定义 entries 不影响共享缓存实例"""
        mine = QaRetriever([QaEntry(id="X1", question="自定义问题", answer="答", tags=["x"])])
        shared = get_qa_retriever()
        assert mine is not shared
        assert len(mine.entries) == 1


class TestReportRagReference:

    def test_reference_uses_qa_bank(self):
        """参考答案优先检索面经库（RAG），而非 expected_points 兜底"""
        q = InterviewQuestion(
            id="T1", type=QuestionType.TECHNICAL, category="Python基础",
            question="解释 Python 的 GIL", expected_points=["GIL定义"],
        )
        ev = EvaluationResult(correctness=6, depth=6, structure=6, relevance=6)
        answers = [{"question": q, "answer": "答", "evaluation": ev, "is_follow_up": False}]

        gen = ReportGenerator(MockLLMClient())

        async def scenario():
            return await gen.generate(JDAnalysis(position="后端工程师"), answers)

        report = run(scenario())
        assert report.reference_answers
        ref = report.reference_answers[0]
        assert ref["source"] == "面经库"
        assert "GIL" in ref["answer"]  # 面经内容是真实参考答案，不是"答题要点："开头

    def test_expected_points_fallback_when_no_match(self):
        """检索不到面经时回退 expected_points 兜底（用与面经库完全无关的英文查询）"""
        q = InterviewQuestion(
            id="T2", type=QuestionType.TECHNICAL, category="冷门方向",
            question="Implement the zzzzqqqq feature", expected_points=["定义", "原理"],
        )
        ev = EvaluationResult(correctness=6, depth=6, structure=6, relevance=6)
        answers = [{"question": q, "answer": "答", "evaluation": ev, "is_follow_up": False}]

        gen = ReportGenerator(MockLLMClient())

        async def scenario():
            return await gen.generate(JDAnalysis(position="后端工程师"), answers)

        report = run(scenario())
        assert report.reference_answers
        assert "答题要点" in report.reference_answers[0]["answer"]
