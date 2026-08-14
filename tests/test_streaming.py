"""
流式输出测试（阶段 2c）
======================
覆盖 core/llm.stream_chat_with_retry 的 usage_stats 计数与重试语义，
以及 report.generate_stream 的事件序列与确定性字段。
全部离线（FakeStreamLLM），CI 可直接运行。
"""

import asyncio

import pytest

from core.llm import LLMClient, LLMResponse
from core.mock_llm import MockLLMClient
from interview.evaluator import EvaluationResult, FollowUpDecision
from interview.interviewer import Interviewer, InterviewPhase
from interview.jd_parser import JDAnalysis
from interview.question_bank import InterviewQuestion, QuestionType
from interview.report import ReportGenerator


def run(coro):
    return asyncio.run(coro)


# ── FakeStreamLLM：逐块 yield 的流式实现 ────────────────────────

class FakeStreamLLM(LLMClient):
    """逐块 yield 的流式 LLM，可配置前 N 次调用抛限流异常（验证重试）"""

    def __init__(self, chunks=None, fail_times: int = 0):
        super().__init__(model="fake-stream")
        self.chunks = list(chunks) if chunks is not None else ["改进", "建议", "内容"]
        self.fail_times = fail_times
        self.calls = 0

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096, stream=False):
        return LLMResponse(
            content="".join(self.chunks),
            usage={"prompt_tokens": 4, "completion_tokens": 4},
        )

    async def stream_chat(self, messages, temperature=0.7, max_tokens=4096):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("RateLimitError: simulated 429")
        for chunk in self.chunks:
            yield chunk


class _FailMidStreamLLM(LLMClient):
    """首块之后抛异常 — 验证流已开始不再重试"""

    def __init__(self):
        super().__init__(model="fake-midstream-fail")
        self.calls = 0

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096, stream=False):
        return LLMResponse(content="ok")

    async def stream_chat(self, messages, temperature=0.7, max_tokens=4096):
        self.calls += 1
        yield "第一块"
        raise RuntimeError("connection error")


# ── stream_chat_with_retry ─────────────────────────────────────

class TestStreamChatWithRetry:

    def test_records_usage_stats(self):
        """流式入口完整消费后应累计 call_count + token（不再漏计）"""
        llm = FakeStreamLLM(chunks=["改进", "建议", "内容"])

        async def scenario():
            out = []
            async for c in llm.stream_chat_with_retry(
                [llm.user_message("面试复盘建议")], max_tokens=100,
            ):
                out.append(c)
            return out

        out = run(scenario())
        assert "".join(out) == "改进建议内容"
        assert llm.usage_stats["call_count"] == 1
        assert llm.usage_stats["prompt_tokens"] > 0
        assert llm.usage_stats["completion_tokens"] > 0

    def test_retries_before_first_chunk(self):
        """首块前限流 → 重试一次 → 恢复（调用 2 次，且只计 1 次用量）"""
        llm = FakeStreamLLM(chunks=["改进"], fail_times=1)

        async def scenario():
            out = []
            async for c in llm.stream_chat_with_retry(
                [llm.user_message("面试复盘建议")], max_retries=2,
            ):
                out.append(c)
            return out

        out = run(scenario())
        assert "".join(out) == "改进"
        assert llm.calls == 2, f"应重试 1 次（共 2 次调用），实际 {llm.calls}"
        assert llm.usage_stats["call_count"] == 1  # 只有成功那次计用量

    def test_no_retry_after_first_chunk(self):
        """流已开始（首块已发出）→ 中途失败不重试，直接抛错"""
        llm = _FailMidStreamLLM()

        async def scenario():
            out = []
            async for c in llm.stream_chat_with_retry(
                [llm.user_message("面试复盘建议")], max_retries=2,
            ):
                out.append(c)

        with pytest.raises(RuntimeError):
            run(scenario())
        assert llm.calls == 1  # 绝不重复输出已发文字


# ── ReportGenerator.generate_stream ────────────────────────────

def _make_answers():
    q = InterviewQuestion(
        id="T1", type=QuestionType.TECHNICAL, category="Python基础",
        question="解释 GIL。", expected_points=["GIL定义"],
    )
    ev = EvaluationResult(
        correctness=8, depth=7, structure=7, relevance=8,
        overall_comment="ok",
        strengths=["基础扎实"], weaknesses=["深度不足"],
        follow_up_decision=FollowUpDecision.MOVE_ON,
    )
    return [{"question": q, "answer": "回答", "evaluation": ev, "is_follow_up": False}]


class TestGenerateStream:

    def test_event_sequence_stats_delta_done(self):
        """事件顺序: stats → (delta...) → done，最终报告含流式叙事"""
        gen = ReportGenerator(FakeStreamLLM(chunks=["改进", "建议", "内容"]))

        async def scenario():
            events = []
            async for e in gen.generate_stream(JDAnalysis(position="后端工程师"), _make_answers()):
                events.append(e)
            return events

        events = run(scenario())
        types = [e["type"] for e in events]
        assert types[0] == "stats"
        assert types[-1] == "done"
        assert "delta" in types

        final = events[-1]["report"]
        assert final.improvement_advice == "改进建议内容"
        assert final.verdict == "推荐通过"  # total_score 7.6 ≥ 7.5

    def test_stream_failure_falls_back(self):
        """流式失败 → 报告不崩溃，退回默认建议"""
        gen = ReportGenerator(_FailMidStreamLLM())

        async def scenario():
            events = []
            async for e in gen.generate_stream(JDAnalysis(position="后端工程师"), _make_answers()):
                events.append(e)
            return events

        events = run(scenario())
        final = events[-1]["report"]
        assert final.improvement_advice  # 降级后仍有建议文案
        assert final.overall_score > 0

    def test_verdict_thresholds(self):
        assert ReportGenerator._verdict_from_score(8.0) == "推荐通过"
        assert ReportGenerator._verdict_from_score(6.0) == "建议待定"
        assert ReportGenerator._verdict_from_score(3.0) == "不推荐通过"

    def test_aggregate_evals_counts_frequency(self):
        answers = _make_answers()
        answers[0]["evaluation"].strengths = ["基础扎实", "表达清晰", "基础扎实"]
        top = ReportGenerator._aggregate_evals(answers, "strengths", ["默认"])
        assert top[0] == "基础扎实"  # 出现 2 次，最频繁

    def test_reference_answers_fallback(self):
        """无 LLM 参考答案时 → 用题库期望要点兜底，保证报告总有参考答案"""
        gen = ReportGenerator(FakeStreamLLM(chunks=["建议"]))

        async def scenario():
            events = []
            async for e in gen.generate_stream(JDAnalysis(position="后端"), _make_answers()):
                events.append(e)
            return events

        events = run(scenario())
        final = events[-1]["report"]
        assert final.reference_answers  # 兜底生成了参考答案
        assert "GIL定义" in final.reference_answers[0]["answer"]
        # 逐题详情也带上答题要点
        assert "GIL定义" in final.details[0]["expected_points"]


# ── 延迟报告集成（defer_report + stream_report） ─────────────────

class TestDeferredReportIntegration:

    def test_defer_report_returns_none_then_streams(self):
        """defer_report=True 时结论分支不内联报告，改由 stream_report 流式产出"""
        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=1, defer_report=True)
            await iv.start("Python 后端工程师，要求 FastAPI MySQL")
            await iv.next_question()
            turn = await iv.skip_question()  # 跳过唯一一题 → 进入结论
            return iv, turn

        iv, turn = run(scenario())
        assert turn.is_finished is True
        assert turn.report is None  # 延迟报告: 结论时不再内联生成

        async def stream():
            events = []
            async for e in iv.stream_report():
                events.append(e)
            return events

        events = run(stream())
        assert events[0]["type"] == "stats"
        assert events[-1]["type"] == "done"
        assert "report" in iv.state.metrics  # report 阶段指标已记录（token 不漏计）
        assert iv.state.phase == InterviewPhase.CONCLUSION
