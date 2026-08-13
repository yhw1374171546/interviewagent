"""
面试模拟 Agent 单元测试
=======================
"""

from __future__ import annotations

import pytest

from core.llm import LLMClient
from interview.evaluator import EvaluationResult, FollowUpDecision
from interview.jd_parser import JDAnalysis, JDParser
from interview.question_gen import InterviewQuestion, QuestionType
from interview.report import ReportGenerator

# ── Stub LLM ──────────────────────────────────────────────────

class StubLLM(LLMClient):
    """
    测试用 LLM，不产生真实 API 调用。

    继承 LLMClient（而非鸭子类型）— 面试链路各模块现在调用
    llm.chat_with_retry()（容错层），继承后自动获得该方法。
    """

    def __init__(self, responses: list | None = None):
        from core.llm import LLMResponse

        super().__init__(model="stub")
        self.responses = responses or [LLMResponse(content="{}")]
        self.call_count = 0

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096, stream=False):
        self.call_count += 1
        idx = min(self.call_count - 1, len(self.responses) - 1)
        return self.responses[idx]


# ── Tests: JD Parser ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_jd_parser_extracts_skills():
    """
    测试 JD 解析: 规则引擎提取技能 + LLM 兜底提取岗位/重点/补充技能。

    注意: LLM 兜底仅在未匹配文本 >50 字符时触发（成本优化），
    所以 JD 文本必须足够长。
    """
    import json

    from core.llm import LLMResponse

    mock_jd = {
        "position": "后端开发工程师",
        "domain_knowledge": ["微服务"],
        "responsibilities": ["架构设计", "性能优化"],
        "interview_focus": ["Python深度", "系统设计", "问题解决"],
        "missing_skills": ["Redis"],  # 规则引擎漏掉的技能，合并进 required
    }

    llm = StubLLM(responses=[LLMResponse(content=json.dumps(mock_jd, ensure_ascii=False))])
    parser = JDParser(llm)

    jd_text = (
        "岗位职责: 负责核心业务系统的后端开发与架构设计工作，参与技术方案评审，"
        "推动性能优化落地，指导初级工程师成长，保障系统稳定运行。\n"
        "任职要求:\n"
        "1. 精通 Python、Django、MySQL\n"
        "2. 了解 Docker 者优先\n"
    )

    result = await parser.parse(jd_text)

    # LLM 兜底提取的岗位名
    assert result.position == "后端开发工程师"
    # 规则引擎提取的技能
    assert "Python" in result.required_skills
    assert "Django" in result.required_skills
    # 「优先」上下文 → 加分技能
    assert "Docker" in result.preferred_skills
    # LLM 补充的技能合并进 required
    assert "Redis" in result.required_skills
    # LLM 推断的考察重点
    assert len(result.interview_focus) == 3


@pytest.mark.asyncio
async def test_jd_parser_handles_malformed_json():
    """测试 JD 解析能容错畸形 JSON（LLM 返回非 JSON 时优雅降级）"""
    from core.llm import LLMResponse

    llm = StubLLM(responses=[LLMResponse(content="这不是 JSON")])
    parser = JDParser(llm)

    # 足够长的 JD 触发 LLM 兜底路径，LLM 返回垃圾 → 不应崩溃
    jd_text = (
        "负责核心业务系统的后端开发与架构设计工作，参与技术方案评审，"
        "推动性能优化落地，指导初级工程师成长，保障系统稳定运行。"
    )
    result = await parser.parse(jd_text)

    # 应该降级返回非空的默认值
    assert isinstance(result, JDAnalysis)
    assert len(result.interview_focus) > 0


# ── Tests: Evaluator ─────────────────────────────────────────

def test_evaluation_result_scoring():
    """测试评分计算"""
    ev = EvaluationResult(
        correctness=8, depth=7, structure=6, relevance=9,
        overall_comment="还行",
    )

    # 加权: 8*0.35 + 7*0.25 + 6*0.20 + 9*0.20 = 2.8 + 1.75 + 1.2 + 1.8 = 7.55
    assert 7.5 <= ev.total_score <= 7.6


def test_evaluation_result_level():
    """测试等级评定"""
    assert EvaluationResult(correctness=9, depth=9, structure=9, relevance=9).level == "🌟 卓越"
    assert EvaluationResult(correctness=8, depth=8, structure=8, relevance=8).level == "✅ 优秀"
    assert EvaluationResult(correctness=6, depth=6, structure=6, relevance=7).level == "👍 良好"
    assert EvaluationResult(correctness=4, depth=4, structure=4, relevance=5).level == "⚠️ 一般"
    assert EvaluationResult(correctness=2, depth=2, structure=2, relevance=2).level == "❌ 需提升"


# ── Tests: Follow-up Decision ─────────────────────────────────

def test_follow_up_decision_enum():
    """测试追问决策枚举"""
    assert FollowUpDecision.DEEPEN == "deepen"
    assert FollowUpDecision.MOVE_ON == "move_on"


# ── Tests: Report ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_report_quick_mode():
    """测试快速报告（不调 LLM）"""
    q1 = InterviewQuestion(
        id="T1", type=QuestionType.TECHNICAL, category="Python",
        question="解释 GIL",
    )
    q2 = InterviewQuestion(
        id="S1", type=QuestionType.SCENARIO, category="系统设计",
        question="设计排行榜",
    )

    answers = [
        {
            "question": q1,
            "answer": "GIL 是...",
            "evaluation": EvaluationResult(
                correctness=8, depth=7, structure=8, relevance=9,
                overall_comment="很好",
            ),
            "is_follow_up": False,
        },
        {
            "question": q2,
            "answer": "用 Redis sorted set...",
            "evaluation": EvaluationResult(
                correctness=6, depth=5, structure=6, relevance=7,
                overall_comment="还行",
            ),
            "is_follow_up": False,
        },
    ]

    gen = ReportGenerator(StubLLM())
    report = gen.quick_report(answers, JDAnalysis())

    assert report.total_questions == 2
    assert 6.5 <= report.overall_score <= 7.5
    assert len(report.details) == 2


# ── Tests: Question ───────────────────────────────────────────

def test_interview_question_dataclass():
    """测试题目数据类"""
    q = InterviewQuestion(
        id="T1",
        type=QuestionType.TECHNICAL,
        category="Python基础",
        question="什么是装饰器？",
        expected_points=["闭包概念", "语法糖", "实际应用"],
        difficulty=3,
    )

    assert q.difficulty == 3
    assert len(q.expected_points) == 3
