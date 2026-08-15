"""
记忆模块接入测试
================
覆盖 1a 的三条链路:
    - 轮内记忆: build_history_summary 摘要生成（确定性、零依赖）
    - 记忆注入: evaluator 评估 prompt 包含历史摘要与弱项提示
    - 跨会话记忆: InterviewMemory 存储/检索 + ChromaDB 缺失时优雅降级

运行:
    pytest tests/test_memory_context.py -v
"""

import asyncio

from core.llm import LLMClient, LLMResponse
from core.mock_llm import MockLLMClient
from interview.evaluator import AnswerEvaluator, EvaluationResult
from interview.memory_context import (
    InterviewMemory,
    MemoryEntry,
    build_history_summary,
)
from interview.question_bank import InterviewQuestion, QuestionType


def run(coro):
    return asyncio.run(coro)


GO_GC_QUESTION = InterviewQuestion(
    id="GO003", type=QuestionType.TECHNICAL, category="Go语言",
    question="Go 的 GC 是如何工作的？它经历了哪些演进（从 STW 到并发三色标记）？",
    expected_points=["三色标记", "写屏障", "混合写屏障", "GC触发条件", "GC调优"],
    difficulty=5,
)


# ═══════════════ 轮内记忆: 历史摘要 ═══════════════

class TestHistorySummary:

    def test_empty_answers(self):
        assert build_history_summary([]) == ""

    def test_summary_content(self):
        q1 = InterviewQuestion(
            id="T1", type=QuestionType.TECHNICAL, category="数据库",
            question="MySQL 索引?", expected_points=["B+树"],
        )
        answers = [
            {
                "question": q1,
                "answer": "B+ 树...",
                "evaluation": EvaluationResult(
                    correctness=5, depth=5, structure=5, relevance=5,
                    overall_comment="一般",
                    weaknesses=["索引原理不深入"],
                    matched_points=["B+树结构"],
                ),
                "is_follow_up": False,
            },
        ]
        summary = build_history_summary(answers)
        assert "数据库" in summary
        assert "5.0" in summary  # 5*0.35+5*0.25+5*0.20+5*0.20 = 5.0
        assert "索引原理不深入" in summary

    def test_skipped_question(self):
        q1 = InterviewQuestion(
            id="T1", type=QuestionType.TECHNICAL, category="Python",
            question="GIL?",
        )
        summary = build_history_summary([{
            "question": q1, "answer": "跳过", "evaluation": None,
            "is_follow_up": False,
        }])
        assert "跳过" in summary

    def test_max_len_truncation(self):
        q1 = InterviewQuestion(
            id="T1", type=QuestionType.TECHNICAL, category="数据库",
            question="索引?",
        )
        answers = [{
            "question": q1, "answer": "x",
            "evaluation": EvaluationResult(
                correctness=5, depth=5, structure=5, relevance=5,
                weaknesses=["非常长的弱点描述" * 50],
            ),
            "is_follow_up": False,
        }]
        assert len(build_history_summary(answers, max_len=100)) <= 100


# ═══════════════ 记忆注入: 评估 prompt ═══════════════

class _CaptureLLM(LLMClient):
    """捕获发给 LLM 的完整 prompt，返回固定评估 JSON"""

    def __init__(self):
        super().__init__(model="fake-capture")
        self.prompts = []

    async def chat(self, messages, **kwargs):
        self.prompts.append(messages[0].content)
        return LLMResponse(
            content='{"depth_level": "深入", "structure_level": "清晰", '
                    '"overall_comment": "ok", "follow_up_decision": "move_on", '
                    '"follow_up_question": "", "strengths": [], "weaknesses": []}',
        )


class TestMemoryInjection:

    def test_history_injected_into_prompt(self):
        """轮内历史摘要应出现在评估 prompt 中"""
        llm = _CaptureLLM()
        evaluator = AnswerEvaluator(llm)

        run(evaluator.evaluate(
            GO_GC_QUESTION,
            "三色标记 写屏障 混合写屏障 GC触发条件 GC调优",
            history_context="第1题(数据库) 6.5分, 弱点: 索引原理不深入",
        ))

        prompt = llm.prompts[-1]
        assert "面试历史" in prompt
        assert "索引原理不深入" in prompt

    def test_memory_hints_injected(self):
        """跨会话弱项提示应出现在评估 prompt 中"""
        llm = _CaptureLLM()
        evaluator = AnswerEvaluator(llm)

        run(evaluator.evaluate(
            GO_GC_QUESTION,
            "三色标记 写屏障 混合写屏障 GC触发条件 GC调优",
            memory_hints=["Go语言类题目历史得分 4.5，需重点考察"],
        ))

        prompt = llm.prompts[-1]
        assert "历史弱项" in prompt
        assert "4.5" in prompt

    def test_no_memory_no_injection(self):
        """未提供记忆时 prompt 不应包含记忆段落"""
        llm = _CaptureLLM()
        evaluator = AnswerEvaluator(llm)

        run(evaluator.evaluate(
            GO_GC_QUESTION,
            "三色标记 写屏障 混合写屏障 GC触发条件 GC调优",
        ))

        prompt = llm.prompts[-1]
        assert "面试历史" not in prompt
        assert "历史弱项" not in prompt


# ═══════════════ 跨会话记忆: 降级与检索 ═══════════════

class TestInterviewMemory:
    """跨会话记忆测试。

    用独立持久化目录隔离真实数据（装 chromadb 后默认目录有历史记忆，
    不隔离会导致测试读到真实数据而失败）。
    """

    def _fresh_memory(self) -> InterviewMemory:
        memory = InterviewMemory(persist_dir="data/chroma_test_ctx")
        # 清空（进程内 + chroma），保证每个测试从干净状态开始
        memory._entries.clear()
        if memory._chroma is not None:
            memory._chroma.clear()
        return memory

    def test_remember_and_recall_weaknesses(self):
        """存储低分记录后能检索到弱项（ChromaDB 缺失时走进程内兜底）"""
        memory = self._fresh_memory()
        memory.remember_answer(MemoryEntry(
            question="Go 的 GC?", answer="不会", score=4.5,
            category="Go语言", question_type="technical",
            skills=["Go语言"],
        ))
        memory.remember_answer(MemoryEntry(
            question="MySQL 索引?", answer="B+树", score=8.5,
            category="数据库", question_type="technical",
            skills=["数据库"],
        ))

        weak = memory.recall_weaknesses(["Go", "golang"], score_threshold=7.0)
        assert len(weak) >= 1, f"应检索到弱项: {weak}"
        assert any("Go" in w for w in weak)

    def test_high_scores_not_recalled(self):
        """高分记录不应出现在弱项检索结果中"""
        memory = self._fresh_memory()
        memory.remember_answer(MemoryEntry(
            question="MySQL 索引?", answer="B+树", score=9.0,
            category="数据库", question_type="technical",
        ))
        weak = memory.recall_weaknesses(["mysql"], score_threshold=7.0)
        assert weak == []

    def test_empty_memory(self):
        """无历史时返回空列表（面试不中断）"""
        memory = self._fresh_memory()
        assert memory.recall_weaknesses(["python"]) == []

    def test_backend_reported(self):
        """后端类型可观测（chroma / memory / none 三选一，绝不报错）"""
        memory = self._fresh_memory()
        assert memory.backend in ("chroma", "memory", "none")

    def test_use_chroma_false_skips_chroma(self):
        """use_chroma=False → 纯内存后端，绝不初始化 chroma（离线工具/benchmark 用）"""
        memory = InterviewMemory(use_chroma=False)
        assert memory._ensure_chroma() is False
        assert memory.backend == "memory"
        assert memory._chroma is None  # 未初始化 chroma 客户端


# ═══════════════ 全链路: 面试 + 记忆（离线） ═══════════════

class TestFullInterviewWithMemory:

    def test_interview_records_memory_offline(self):
        """完整面试（Mock LLM）结束后，记忆里应有多条记录"""
        from interview.interviewer import Interviewer

        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=2)
            await iv.run_full_interview(
                "Go 后端工程师，要求 golang 微服务",
                answers=[
                    "Go 的 GMP 调度模型中 G 是 goroutine，M 是线程，P 是处理器，"
                    "阻塞时会把 P 转交给其他 M，通过 work stealing 均衡负载。",
                    "channel 底层是 hchan 结构，包含环形队列和 sudog 等待队列。",
                ],
            )
            return iv

        iv = run(scenario())
        assert iv.memory.entry_count >= 2, "面试中每题都应写入记忆"
