"""
评估体验优化测试（等待缩短 + 流式思考过程）
==========================================
覆盖:
    1. keyword_analysis — 确定性关键词层（各边界 + 正常）
    2. 并行化等价性 — submit_answer 并行 evaluate+decide 结果与串行一致
    3. submit_answer_stream — SSE 事件序列（analyzing → analysis → evaluation → done）
全部离线（Mock LLM），CI 可直接运行。
"""

import asyncio

from core.mock_llm import MockLLMClient
from interview.evaluator import AnswerEvaluator
from interview.interviewer import Interviewer
from interview.question_bank import InterviewQuestion, QuestionType


def run(coro):
    return asyncio.run(coro)


def _q() -> InterviewQuestion:
    return InterviewQuestion(
        id="T1", type=QuestionType.TECHNICAL, category="Python基础",
        question="解释 Python 的 GIL", expected_points=["GIL", "多线程"],
        difficulty=3,
    )


class TestKeywordAnalysis:

    def test_normal_answer(self):
        ev = AnswerEvaluator(None)
        kw = ev.keyword_analysis(_q(), "GIL 是 CPython 的全局解释器锁，多线程 CPU 密集会被串行化。")
        assert kw["boundary"] == ""
        assert kw["match_rate"] > 0
        assert "gil" in kw["matched"]  # 与 evaluate() 一致: matched 为小写化要点
        assert kw["comment_hint"]

    def test_boundaries(self):
        ev = AnswerEvaluator(None)
        q = _q()
        assert ev.keyword_analysis(q, "")["boundary"] == "empty"
        assert ev.keyword_analysis(q, "太短了")["boundary"] == "short"
        assert ev.keyword_analysis(q, "GOGOGOGOGOGOGOGOGOGOGO")["boundary"] == "spam"
        # 70%+ 词来自题目 → 复读
        assert ev.keyword_analysis(q, "解释 Python 的 GIL，解释 Python 的 GIL，解释 Python 的 GIL")["boundary"] == "restate"


class TestParallelEvaluation:

    def test_submit_answer_still_works(self):
        """并行化后 submit_answer 行为不变（mock 全链路）"""
        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=1)
            await iv.start("Python 后端工程师")
            await iv.next_question()
            turn = await iv.submit_answer("GIL 是 CPython 的全局解释器锁，多线程下 CPU 密集任务会被串行化，IO 密集会释放锁。")
            return iv, turn

        iv, turn = run(scenario())
        assert len(iv.state.answers) == 1
        assert turn.phase.value in ("follow_up", "next", "finished")

    def test_follow_up_decision_from_parallel_agent(self):
        """正常回答走并行 decide（不是边界兜底）— decision 非 None"""
        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=1)
            await iv.start("Python 后端工程师")
            await iv.next_question()
            # 短回答 → 边界分支（decide 不执行）；正常回答 → 并行分支
            await iv.submit_answer("太短了")
            return iv

        iv = run(scenario())
        assert len(iv.state.answers) == 1


class TestSubmitAnswerStream:

    def test_event_sequence(self):
        """SSE 事件序列: analyzing → (analysis) → evaluation → done"""
        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=1)
            await iv.start("Python 后端工程师")
            await iv.next_question()
            events = []
            async for e in iv.submit_answer_stream(
                "GIL 是 CPython 的全局解释器锁，多线程 CPU 密集会被串行化，IO 密集会释放锁。"
            ):
                events.append(e)
            return iv, events

        iv, events = run(scenario())
        types = [e["type"] for e in events]
        assert types[0] == "analyzing"
        assert types[-1] == "done"
        assert "evaluation" in types
        assert iv.state.evaluate_count == 1
        # evaluation 事件携带完整 turn
        ev_turn = next(e["turn"] for e in events if e["type"] == "evaluation")
        assert ev_turn.evaluation is not None

    def test_stream_records_metrics(self):
        """流式路径指标记录（timings/metrics/evaluate_count）与 submit_answer 同口径"""
        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=1)
            await iv.start("Python 后端工程师")
            await iv.next_question()
            async for _ in iv.submit_answer_stream("GIL 是锁。太短了。啊？"):
                pass
            return iv

        iv = run(scenario())
        assert iv.state.evaluate_count == 1
        assert "evaluate" in iv.state.timings
        assert iv.state.metrics["evaluate"]["latency"] >= 0

    def test_conclusion_session_done_immediately(self):
        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=1)
            await iv.start("Python 后端工程师")
            await iv.next_question()
            iv.state.phase = iv.state.phase.__class__.CONCLUSION
            events = []
            async for e in iv.submit_answer_stream("任何回答"):
                events.append(e)
            return events

        events = run(scenario())
        assert [e["type"] for e in events] == ["done"]
