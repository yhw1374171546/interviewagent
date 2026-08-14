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
from interview.qa_bank import QaRetriever
from interview.question_bank import InterviewQuestion, QuestionType
from interview.report import ReportGenerator


def run(coro):
    return asyncio.run(coro)


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
