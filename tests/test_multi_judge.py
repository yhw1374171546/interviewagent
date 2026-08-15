"""
多评委仲裁评估测试
==================
覆盖 interview/multi_judge.py:
    - 分歧小 → 取平均（不仲裁）
    - 分歧大 → 仲裁 Agent 裁决
    - 单评委失败 → 降级用另一评委
    - 双评委失败 → 中性兜底
    - 仲裁失败 → 取均分（不中断）
    - 与 AnswerEvaluator 集成（multi_judge 参数开关）

全离线（Stub LLM），CI 可直接运行。
"""

import asyncio

from core.llm import LLMClient, LLMResponse
from interview.evaluator import AnswerEvaluator
from interview.multi_judge import MultiJudge
from interview.question_bank import InterviewQuestion, QuestionType


def run(coro):
    return asyncio.run(coro)


def _question() -> InterviewQuestion:
    return InterviewQuestion(
        id="T1", type=QuestionType.TECHNICAL, category="Python",
        question="请解释 Python 的 GIL",
        expected_points=["GIL定义", "多进程", "asyncio"],
        difficulty=3,
    )


# ── Stub LLM: 按调用序号返回预设响应 ───────────────────────────

class _SeqLLM(LLMClient):
    """按调用顺序返回预设内容。每次调用: content 或 tool_calls"""

    def __init__(self, responses: list[str]):
        super().__init__(model="seq")
        self.responses = responses
        self.calls = 0

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096, stream=False):
        self.calls += 1
        idx = min(self.calls - 1, len(self.responses) - 1)
        return LLMResponse(content=self.responses[idx], usage={"prompt_tokens": 5, "completion_tokens": 5})


# 评委 JSON（depth_level/structure_level 不同 → 触发仲裁）
JUDGE_A_JSON = '{"depth_level": "深入", "structure_level": "清晰", "overall_comment": "A", "follow_up_decision": "move_on"}'
JUDGE_B_JSON = '{"depth_level": "较浅", "structure_level": "一般", "overall_comment": "B", "follow_up_decision": "move_on"}'
ARBITER_JSON = '{"depth_level": "适中", "structure_level": "清晰", "overall_comment": "裁决", "follow_up_decision": "move_on"}'


class TestMultiJudge:

    def test_low_disagreement_average(self):
        """分歧小（同分）→ 取平均，不仲裁，2 次调用"""
        # 两评委都是"适中/清晰"→ depth 6, struct 7
        j = '{"depth_level": "适中", "structure_level": "清晰", "overall_comment": "ok", "follow_up_decision": "move_on"}'
        llm = _SeqLLM([j, j])
        mj = MultiJudge(llm)
        depth, structure, data, meta = run(mj.evaluate(_question(), "答案"))
        assert meta["disagreement"] == "low"
        assert meta["arbitrated"] is False
        assert depth == 6 and structure == 7
        assert llm.calls == 2  # 只调了两位评委

    def test_high_disagreement_arbitrates(self):
        """分歧大 → 仲裁 Agent 裁决（3 次调用）"""
        llm = _SeqLLM([JUDGE_A_JSON, JUDGE_B_JSON, ARBITER_JSON])
        mj = MultiJudge(llm)
        depth, structure, data, meta = run(mj.evaluate(_question(), "答案"))
        assert meta["disagreement"] == "high"
        assert meta["arbitrated"] is True
        # 仲裁结果: 适中/清晰 → 6/7
        assert depth == 6 and structure == 7
        assert llm.calls == 3
        assert "裁决" in data["overall_comment"]

    def test_one_judge_fails_degrades(self):
        """评委 A 失败（返回垃圾）→ 用评委 B 的结果，不中断"""
        llm = _SeqLLM(["不是JSON", JUDGE_A_JSON])
        mj = MultiJudge(llm)
        depth, structure, data, meta = run(mj.evaluate(_question(), "答案"))
        # 评委 B 是"深入/清晰"→ 8/7
        assert depth == 8 and structure == 7
        assert meta["disagreement"] == "low"

    def test_both_judges_fail_neutral(self):
        """双评委都失败 → 中性 5/5 兜底，不崩溃"""
        llm = _SeqLLM(["垃圾1", "垃圾2"])
        mj = MultiJudge(llm)
        depth, structure, data, meta = run(mj.evaluate(_question(), "答案"))
        assert depth == 5 and structure == 5

    def test_arbiter_fails_averages(self):
        """仲裁失败（返回垃圾）→ 取两位评委均分，不中断"""
        llm = _SeqLLM([JUDGE_A_JSON, JUDGE_B_JSON, "仲裁垃圾"])
        mj = MultiJudge(llm)
        depth, structure, data, meta = run(mj.evaluate(_question(), "答案"))
        assert meta["disagreement"] == "high"
        assert meta["arbitrated"] is False
        # A(深入=8) B(较浅=5) → 均分 6.5 → round 6
        assert depth == 6


# ── 与 AnswerEvaluator 集成 ────────────────────────────────────

class TestEvaluatorMultiJudge:

    def test_multi_judge_enabled(self):
        """传入 multi_judge → LLM 评估走多评委（评语带多评委标记）"""
        j = '{"depth_level": "适中", "structure_level": "清晰", "overall_comment": "ok", "follow_up_decision": "move_on"}'
        llm = _SeqLLM([j, j])
        mj = MultiJudge(llm)
        ev = AnswerEvaluator(llm, multi_judge=mj)
        result = run(ev.evaluate(_question(), "GIL 是全局解释器锁，多进程可绕过，asyncio 处理 IO"))
        assert result.depth >= 5
        assert result.total_score >= 4

    def test_multi_judge_disabled_default(self):
        """不传 multi_judge → 保持单评委（不破坏既有行为）"""
        llm = _SeqLLM([JUDGE_A_JSON])
        ev = AnswerEvaluator(llm)
        result = run(ev.evaluate(_question(), "GIL 是全局解释器锁，多进程可绕过，asyncio 处理 IO"))
        assert result.depth >= 5
