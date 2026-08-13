"""
题目生成器测试（阶段 3）
======================
覆盖题库检索出题、五类配比、LLM 微调/补充失败降级、通识兜底、编号去重。
全部离线（FakeLLM），CI 可直接运行。
"""

import asyncio

from core.llm import LLMClient, LLMResponse
from interview.jd_parser import JDAnalysis
from interview.question_bank import QuestionType
from interview.question_gen import DEFAULT_WEIGHTS, QuestionGenerator


def run(coro):
    return asyncio.run(coro)


class _CrashLLM(LLMClient):
    """调用即抛异常的 LLM（验证微调/补充的降级路径）"""

    def __init__(self):
        super().__init__(model="fake-crash")

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096, stream=False):
        raise RuntimeError("boom")


class _EmptyListLLM(LLMClient):
    """微调/补充返回空列表（表示不修改/不补充）"""

    def __init__(self):
        super().__init__(model="fake-empty")

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096, stream=False):
        return LLMResponse(content="[]")


def _jd(skills=None):
    return JDAnalysis(
        required_skills=skills or ["python", "mysql"],
        preferred_skills=[],
        soft_skills=["沟通"],
        interview_focus=["系统设计"],
    )


class TestGenerate:

    def test_no_llm_returns_bank_questions(self):
        gen = QuestionGenerator(None)
        plan = run(gen.generate(_jd(), total_questions=8))
        assert len(plan.questions) == 8
        assert plan.question_count == 8

    def test_ids_renumbered_unique(self):
        gen = QuestionGenerator(None)
        plan = run(gen.generate(_jd(), total_questions=8))
        ids = [q.id for q in plan.questions]
        assert len(ids) == len(set(ids))
        # 编号带类型前缀
        assert any(q.id.startswith("T") for q in plan.questions)

    def test_all_types_valid(self):
        gen = QuestionGenerator(None)
        plan = run(gen.generate(_jd(), total_questions=8))
        assert all(q.type in QuestionType for q in plan.questions)

    def test_llm_customize_failure_degrades(self):
        """LLM 微调抛异常 → 题库原题照常返回，不崩溃"""
        gen = QuestionGenerator(_CrashLLM())
        plan = run(gen.generate(_jd(), total_questions=8))
        assert len(plan.questions) == 8

    def test_empty_list_llm_keeps_original(self):
        """LLM 微调返回空列表 → 题目措辞不变"""
        gen = QuestionGenerator(_EmptyListLLM())
        plan = run(gen.generate(_jd(), total_questions=8))
        assert all(q.source == "bank" for q in plan.questions)


class TestDefaults:

    def test_default_weights_sum_to_one(self):
        total = sum(DEFAULT_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9

    def test_default_weights_five_types(self):
        assert set(DEFAULT_WEIGHTS) == {
            QuestionType.TECHNICAL,
            QuestionType.SCENARIO,
            QuestionType.PROJECT,
            QuestionType.BEHAVIORAL,
            QuestionType.CODING,
        }


class TestParseJson:

    def test_markdown_fence(self):
        gen = QuestionGenerator(None)
        assert gen._parse_json('```json\n[{"id": "T1"}]\n```') == [{"id": "T1"}]

    def test_invalid_returns_none(self):
        gen = QuestionGenerator(None)
        assert gen._parse_json("not json") is None


class TestLLMGenerateExtra:

    def test_crash_returns_empty(self):
        gen = QuestionGenerator(_CrashLLM())
        result = run(gen._llm_generate_extra(_jd(), needed=2, exclude_ids=set()))
        assert result == []

    def test_empty_list_returns_empty(self):
        gen = QuestionGenerator(_EmptyListLLM())
        result = run(gen._llm_generate_extra(_jd(), needed=2, exclude_ids=set()))
        assert result == []
