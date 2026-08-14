"""
追问自主决策 Agent 测试（阶段：追问 Agent 化）
=============================================
覆盖 FollowUpAgent.decide 的三种结果（继续追问/停止/降级），
以及 interviewer 接入后追问决策优先走 Agent。
全部离线（FakeLLM / Mock），CI 可直接运行。
"""

import asyncio

from core.llm import LLMClient, LLMResponse
from core.mock_llm import MockLLMClient
from interview.evaluator import EvaluationResult, FollowUpDecision
from interview.follow_up_agent import FollowUpAgent
from interview.interviewer import Interviewer
from interview.question_bank import InterviewQuestion, QuestionType


def run(coro):
    return asyncio.run(coro)


def _question():
    return InterviewQuestion(
        id="T1", type=QuestionType.TECHNICAL, category="Python基础",
        question="解释 GIL", expected_points=["GIL定义", "CPU密集vsIO密集"],
    )


def _evaluation():
    return EvaluationResult(
        correctness=6, depth=6, structure=6, relevance=6,
        overall_comment="部分命中",
        matched_points=["GIL定义"], missed_points=["CPU密集vsIO密集"],
        follow_up_decision=FollowUpDecision.MOVE_ON,
    )


class _FakeLLM(LLMClient):
    """返回固定 JSON 的 LLM"""

    def __init__(self, content: str):
        super().__init__(model="fake-followup")
        self.content = content

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096, stream=False):
        return LLMResponse(content=self.content)


class _CrashLLM(LLMClient):
    def __init__(self):
        super().__init__(model="fake-crash")

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096, stream=False):
        raise RuntimeError("boom")


class TestFollowUpAgent:

    def test_continue_true(self):
        agent = FollowUpAgent(_FakeLLM('{"continue": true, "question": "展开说说", "reason": "要点未覆盖"}'))
        d = run(agent.decide(_question(), "回答", _evaluation()))
        assert d["continue_follow_up"] is True
        assert d["question"] == "展开说说"

    def test_continue_false(self):
        agent = FollowUpAgent(_FakeLLM('{"continue": false, "question": "", "reason": "充分"}'))
        d = run(agent.decide(_question(), "回答", _evaluation()))
        assert d["continue_follow_up"] is False

    def test_crash_returns_none_for_fallback(self):
        """LLM 异常 → 返回 None，由调用方回退评估器 5 分类，不中断面试"""
        agent = FollowUpAgent(_CrashLLM())
        d = run(agent.decide(_question(), "回答", _evaluation()))
        assert d["continue_follow_up"] is None

    def test_invalid_json_returns_none(self):
        agent = FollowUpAgent(_FakeLLM("不是 JSON"))
        d = run(agent.decide(_question(), "回答", _evaluation()))
        assert d["continue_follow_up"] is None

    def test_markdown_json_parsed(self):
        agent = FollowUpAgent(_FakeLLM('```json\n{"continue": true, "question": "q", "reason": "r"}\n```'))
        d = run(agent.decide(_question(), "回答", _evaluation()))
        assert d["continue_follow_up"] is True
        assert d["question"] == "q"


class TestInterviewerIntegration:

    def test_interviewer_has_follow_up_agent(self):
        """interviewer 应内置 FollowUpAgent（快模型）"""
        iv = Interviewer(MockLLMClient(), total_questions=1)
        assert isinstance(iv.follow_up_agent, FollowUpAgent)

    def test_full_interview_runs_with_agent(self):
        """追问 Agent 化后完整面试仍可跑通（Mock 模式，确定性）"""
        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=2)
            await iv.run_full_interview(
                "Python 后端工程师，要求 FastAPI MySQL",
                answers=[
                    "MySQL 索引用 B+ 树实现，减少磁盘 IO。",
                    "Redis 单线程事件循环和 IO 多路复用。",
                ],
            )
            return iv

        iv = run(scenario())
        assert iv.state.is_finished is True
