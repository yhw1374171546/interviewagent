"""
评估思维链（CoT）测试（P0: 评估可解释性）
========================================
覆盖:
    1. 单评委路径: LLM 返回 analysis（结构化逐步分析）+ reasoning_content
       （DeepSeek 原始思维链）→ EvaluationResult 正确保存
    2. 多评委路径: 双评委带 analysis + reasoning → meta 携带最终思维链，
       llm_data 保留 analysis
    3. prompts: deep_eval 默认 v2（含 analysis 字段），A/B 可切回 v1
全部离线（FakeLLM），CI 可直接运行。
"""

import asyncio
import json

from core.llm import LLMClient, LLMResponse
from interview.evaluator import AnswerEvaluator
from interview.multi_judge import MultiJudge
from interview.prompts import (
    PROMPT_REGISTRY,
    active_prompt,
    prompt_version,
    render_prompt,
    set_prompt_version,
)
from interview.question_bank import InterviewQuestion, QuestionType


def run(coro):
    return asyncio.run(coro)


def _question() -> InterviewQuestion:
    return InterviewQuestion(
        id="T1", type=QuestionType.TECHNICAL, category="Python基础",
        question="解释 Python 的 GIL", expected_points=["GIL", "多线程"],
        difficulty=3,
    )


_ANSWER = "GIL 是 CPython 的全局解释器锁，多线程 CPU 密集会被串行化，IO 密集会释放锁。"


def _eval_json(analysis: str = "逐步分析: 覆盖了 GIL 定义，但未讲根因。") -> str:
    return json.dumps({
        "analysis": analysis,
        "depth_level": "较浅",
        "structure_level": "一般",
        "overall_comment": "答到了要点，但缺少根因分析",
        "strengths": ["答到要点"],
        "weaknesses": ["缺根因"],
        "follow_up_decision": "deepen",
        "follow_up_question": "为什么 CPython 需要 GIL？",
        "follow_up_reason": "缺根因",
    }, ensure_ascii=False)


class _CoTLLM(LLMClient):
    """返回固定评估 JSON + reasoning_content 的 Fake LLM"""

    def __init__(self, content: str, reasoning: str = "模型思考: GIL 是锁……"):
        super().__init__(model="fake-cot")
        self.content = content
        self.reasoning = reasoning

    async def chat(self, messages, tools=None, temperature=0.7,
                   max_tokens=4096, stream=False):
        return LLMResponse(content=self.content, reasoning_content=self.reasoning)


class TestSingleJudgeCoT:

    def test_analysis_saved_from_json(self):
        """LLM 返回 analysis 字段 → 存入 EvaluationResult（可解释性）"""
        llm = _CoTLLM(_eval_json("逐步分析: 要点全覆盖，深度不足。"))
        ev = run(AnswerEvaluator(llm).evaluate(_question(), _ANSWER))
        assert ev.analysis == "逐步分析: 要点全覆盖，深度不足。"
        assert ev.reasoning_text == "模型思考: GIL 是锁……"

    def test_analysis_empty_when_not_provided(self):
        """LLM 未返回 analysis（如旧模型）→ 空字符串不崩溃"""
        plain = json.dumps({
            "depth_level": "适中", "structure_level": "一般",
            "overall_comment": "可以", "strengths": [], "weaknesses": [],
            "follow_up_decision": "move_on", "follow_up_question": "",
            "follow_up_reason": "充分",
        }, ensure_ascii=False)
        ev = run(AnswerEvaluator(_CoTLLM(plain)).evaluate(_question(), _ANSWER))
        assert ev.analysis == ""
        assert ev.reasoning_text  # reasoning 仍保存

    def test_failure_degrades_with_empty_reasoning(self):
        """LLM 异常降级 → analysis/reasoning 为空字符串"""
        class _Boom(LLMClient):
            async def chat(self, messages, tools=None, temperature=0.7,
                           max_tokens=4096, stream=False):
                raise RuntimeError("api down")

        ev = run(AnswerEvaluator(_Boom("t")).evaluate(_question(), _ANSWER))
        assert ev.analysis == ""
        assert ev.reasoning_text == ""


class TestMultiJudgeCoT:

    def test_meta_carries_reasoning_and_analysis(self):
        """多评委: 双评委带 analysis + reasoning → meta.reasoning 携带，analysis 保留"""
        judge = MultiJudge(_CoTLLM(_eval_json("评委分析内容")))
        depth, struct, llm_data, meta = run(judge.evaluate(_question(), _ANSWER))
        assert llm_data.get("analysis") == "评委分析内容"
        assert meta.get("reasoning")  # 评委思维链透传
        assert meta["reasoning"] == "模型思考: GIL 是锁……"

    def test_evaluator_with_multi_judge_saves_reasoning(self):
        """完整链路: evaluator + multi_judge → EvaluationResult 保存 reasoning/analysis"""
        evaluator = AnswerEvaluator(
            _CoTLLM(_eval_json()), multi_judge=MultiJudge(_CoTLLM(_eval_json())),
        )
        ev = run(evaluator.evaluate(_question(), _ANSWER))
        assert ev.analysis  # 来自 llm_data.analysis
        assert ev.reasoning_text  # 来自 meta.reasoning


class TestPromptCoTVersion:

    def test_deep_eval_defaults_to_v2(self):
        """deep_eval 默认 v2（结构化 CoT 版本，含 analysis 字段要求）"""
        assert prompt_version("deep_eval") == "v2"
        text = active_prompt("deep_eval")
        assert "analysis" in text
        assert "逐步分析" in text

    def test_deep_eval_v2_renders(self):
        """v2 渲染含 analysis 输出格式"""
        rendered = render_prompt(
            "deep_eval", question="Q", expected_points="A", answer="答",
        )
        assert '"analysis"' in rendered

    def test_ab_switch_back_to_v1(self):
        """A/B: 切回 v1（无 analysis 字段）仍可渲染"""
        PROMPT_REGISTRY["deep_eval"]["active"] = "v2"  # 确保初始 v2
        try:
            set_prompt_version("deep_eval", "v1")
            assert "analysis" not in active_prompt("deep_eval")
            render_prompt("deep_eval", question="Q", expected_points="A", answer="答")
        finally:
            set_prompt_version("deep_eval", "v2")
        assert prompt_version("deep_eval") == "v2"
