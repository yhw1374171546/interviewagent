"""
评分校准测试（C1）
==================
覆盖 interview/score_calibration.py:
    - 高分低估 → 校准加分
    - 低分高估 → 校准减分
    - 命中率与评分一致 → 不校准
    - 幅度封顶 / 分数钳制
    - 与 AnswerEvaluator 集成（calibrate 开关 / 默认关闭）

全离线（确定性规则 + Mock LLM），CI 可直接运行。
"""

import asyncio

from core.mock_llm import MockLLMClient
from interview.evaluator import AnswerEvaluator
from interview.question_bank import InterviewQuestion, QuestionType
from interview.score_calibration import _expected_score, calibrate_score


def run(coro):
    return asyncio.run(coro)


class TestCalibrateScore:

    def test_high_match_low_score_upgrades(self):
        """命中率 0.8 但 LLM 给 6 分 → 校准加分（期望≈7.8，gap 1.8→int 1）"""
        score, meta = calibrate_score(6, 0.8)
        assert meta["adjusted"] is True
        assert meta["direction"] == "up"
        assert score == 7  # 6 + int(1.8) = 7
        assert "命中率" in meta["reason"]

    def test_low_match_high_score_downgrades(self):
        """命中率 0.2 但 LLM 给 8 分 → 校准减分"""
        score, meta = calibrate_score(8, 0.2)
        assert meta["adjusted"] is True
        assert meta["direction"] == "down"
        assert score < 8

    def test_consistent_no_adjust(self):
        """命中率与评分一致 → 不校准"""
        score, meta = calibrate_score(7, 0.8)  # 期望 7.8, gap=0.8 < 1.5
        assert meta["adjusted"] is False
        assert score == 7

    def test_none_match_rate_no_adjust(self):
        score, meta = calibrate_score(6, None)
        assert meta["adjusted"] is False

    def test_clamped_to_range(self):
        """校准后分数钳制在 [1,10]"""
        score, meta = calibrate_score(10, 0.9)  # 已满分不超
        assert score <= 10
        score2, _ = calibrate_score(1, 0.1)
        assert score2 >= 1

    def test_expected_score_mapping(self):
        assert _expected_score(0.0) == 3
        assert _expected_score(1.0) == 9
        assert _expected_score(0.5) == 6


# ── 评估器集成 ─────────────────────────────────────────────────

def _question() -> InterviewQuestion:
    return InterviewQuestion(
        id="PY001", type=QuestionType.TECHNICAL, category="Python",
        question="请解释 Python 的 GIL",
        expected_points=["GIL定义", "CPU密集vsIO密集", "multiprocessing", "C扩展", "asyncio"],
        difficulty=3,
    )


# 覆盖全部 5 个要点的高命中回答
FULL_ANSWER = (
    "GIL 定义：全局解释器锁，同一时刻只有一个线程执行字节码。"
    "CPU 密集任务会被 GIL 卡住无法利用多核，IO 密集任务影响较小。"
    "绕过方案：multiprocessing 多进程每个进程独立 GIL，C 扩展释放 GIL，"
    "asyncio 处理 IO 密集。我在项目里用 celery 多进程 worker 绕过 GIL。"
)


class _StubLLM(MockLLMClient):
    """Mock 但可覆盖评分（模拟 LLM 给低分的情况）"""

    def __init__(self, score_json: str | None = None):
        super().__init__()
        self._score_json = score_json

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096, stream=False):
        from core.llm import LLMResponse

        if self._score_json:
            return LLMResponse(content=self._score_json, usage={"prompt_tokens": 5, "completion_tokens": 5})
        return await super().chat(messages, tools, temperature, max_tokens, stream)


class TestEvaluatorCalibration:

    def test_calibration_upgrades_high_match(self):
        """命中率高但 LLM 给分低 → 校准后总分提升"""
        # LLM 深度评估给"较浅/松散"（低分），但关键词命中率高 → 校准
        low_llm = '{"depth_level": "较浅", "structure_level": "松散", "overall_comment": "一般", "follow_up_decision": "move_on"}'
        llm = _StubLLM(low_llm)
        ev = AnswerEvaluator(llm, calibrate=True)
        result = run(ev.evaluate(_question(), FULL_ANSWER))
        # FULL_ANSWER 命中全部要点 → match_rate 高 → 校准加分
        assert "评分校准" in result.overall_comment or result.correctness >= 6

    def test_calibration_disabled_default(self):
        """默认 calibrate=False → 不校准（保持既有行为）"""
        low_llm = '{"depth_level": "较浅", "structure_level": "松散", "overall_comment": "一般", "follow_up_decision": "move_on"}'
        llm = _StubLLM(low_llm)
        ev = AnswerEvaluator(llm)
        result = run(ev.evaluate(_question(), FULL_ANSWER))
        assert "评分校准" not in result.overall_comment

    def test_calibration_with_mock_llm(self):
        """Mock LLM 下校准不崩溃"""
        ev = AnswerEvaluator(MockLLMClient(), calibrate=True)
        result = run(ev.evaluate(_question(), FULL_ANSWER))
        assert result.total_score >= 1


# ── Interviewer 集成（A2 能力接入）──────────────────────────────

class TestInterviewerCalibration:

    def test_calibrate_parameter_passed_to_evaluator(self):
        """Interviewer(calibrate=True) → evaluator.calibrate_enabled=True"""
        from interview.interviewer import Interviewer
        from interview.memory_context import InterviewMemory

        iv = Interviewer(
            MockLLMClient(), total_questions=1,
            memory=InterviewMemory(use_chroma=False),
            calibrate=True,
        )
        assert iv.evaluator.calibrate_enabled is True

    def test_calibrate_default_off(self):
        """默认 calibrate=False → 向后兼容"""
        from interview.interviewer import Interviewer
        from interview.memory_context import InterviewMemory

        iv = Interviewer(
            MockLLMClient(), total_questions=1,
            memory=InterviewMemory(use_chroma=False),
        )
        assert iv.evaluator.calibrate_enabled is False
