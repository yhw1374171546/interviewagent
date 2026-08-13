"""
面试主控
========
管理面试的完整生命周期：开场 → 出题 → 评估追问 → 下一题 → 结束报告

状态机:
    INIT → WARMUP → QUESTION → WAIT_ANSWER → EVALUATE
       ↑                                    ↓
       └──────── FOLLOW_UP ←─────────────────
       │                                    ↓ (move_on / max_followups)
       └──────────────────────────────── NEXT_QUESTION
                                           ↓
                                       CONCLUSION

使用:
    interviewer = Interviewer(llm_client)
    result = await interviewer.start(jd_text)
    # 交互式: print(result.question)
    # answer = input("你的回答: ")
    # result = await interviewer.submit_answer(answer)
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum

from core.llm import LLMClient, Message, Role

from .evaluator import AnswerEvaluator, EvaluationResult, FollowUpDecision
from .jd_parser import JDAnalysis, JDParser
from .question_bank import InterviewQuestion, QuestionType
from .question_gen import InterviewPlan, QuestionGenerator
from .report import InterviewReport, ReportGenerator

# ── 序列化工具（Web 断点恢复用）────────────────────────────────

def _jsonable(obj):
    """递归把 dataclass/Enum 转为 JSON 可序列化的 dict/基础类型"""
    if dataclasses.is_dataclass(obj):
        return {k: _jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x) for x in obj]
    return obj


# ── 状态机 ───────────────────────────────────────────────────

class InterviewPhase(str, Enum):
    INIT = "init"               # 初始化
    WARMUP = "warmup"           # 暖场介绍
    QUESTION = "question"       # 提问（等待回答）
    WAIT_ANSWER = "wait_answer" # 等待面试者输入
    EVALUATE = "evaluate"       # 评估中
    FOLLOW_UP = "follow_up"     # 追问
    NEXT_QUESTION = "next"      # 进入下一题
    CONCLUSION = "conclusion"   # 结束


# ── 交互状态 ─────────────────────────────────────────────────

@dataclass
class InterviewState:
    """面试会话的完整状态"""
    # JD 相关
    jd_text: str = ""
    jd_analysis: JDAnalysis = field(default_factory=JDAnalysis)

    # 题目
    plan: InterviewPlan = field(default_factory=InterviewPlan)
    current_question_index: int = -1
    current_question: InterviewQuestion | None = None

    # 回答记录
    answers: list[dict] = field(default_factory=list)  # [{question, answer, evaluation}]
    current_follow_up_count: int = 0
    max_follow_ups: int = 3  # 每题最多追问次数

    # 状态
    phase: InterviewPhase = InterviewPhase.INIT
    interview_started: bool = False

    @property
    def current_question_num(self) -> int:
        return self.current_question_index + 1

    @property
    def total_questions(self) -> int:
        return len(self.plan.questions)

    @property
    def progress(self) -> str:
        """进度条文字"""
        if not self.plan.questions:
            return ""
        return f"第 {self.current_question_num}/{self.total_questions} 题"

    @property
    def is_finished(self) -> bool:
        return self.phase == InterviewPhase.CONCLUSION


# ── 交互结果 ─────────────────────────────────────────────────

@dataclass
class TurnResult:
    """每一轮对话的结果（返回给 UI 层）"""
    phase: InterviewPhase
    message: str                          # 显示给面试者的文字
    question: InterviewQuestion | None = None  # 当前题目（如果有）
    evaluation: EvaluationResult | None = None # 上一轮的评估（如果有）
    report: InterviewReport | None = None      # 最终报告（结束时）
    progress: str = ""                    # 进度文字
    is_finished: bool = False


# ── 面试官 ───────────────────────────────────────────────────

WARMUP_PROMPT = """你是一位专业的面试官，现在开始一场模拟面试。请根据以下岗位信息，为面试者做一个简短的面试开场介绍。

## 岗位信息
- 岗位: {position}
- 核心要求: {skills}

## 开场白要求
1. 自我介绍（你是什么岗位的面试官）
2. 说明今天的面试流程和大致的题目数量
3. 一句话让面试者放松

请用友好、专业的语气。字数控制在 100 字以内。直接输出开场白，不要写"面试官："之类的角色标注。"""


class Interviewer:
    """
    面试官 — 面试主控类。

    管理从 JD 解析到最终报告生成的完整体面流程。

    使用方式（交互式）:
        interviewer = Interviewer(llm_client)

        # Step 1: 初始化
        turn = await interviewer.start(jd_text)
        print(turn.message)  # 暖场开场白

        # Step 2: 循环问答
        while not turn.is_finished:
            answer = input("你的回答: ")
            turn = await interviewer.submit_answer(answer)
            if turn.evaluation:
                print(f"得分: {turn.evaluation.total_score}/10")
            print(turn.message)

        # Step 3: 查看报告
        print(interviewer.report_text())

    使用方式（一次性 — 适合测试/演示）:
        interviewer = Interviewer(llm_client)
        report = await interviewer.run_full_interview(jd_text, answers=["...", "..."])
    """

    def __init__(
        self,
        llm_client: LLMClient,
        total_questions: int = 8,
        max_follow_ups: int = 3,
    ):
        self.llm = llm_client
        self.total_questions = total_questions
        self.max_follow_ups = max_follow_ups

        # 子模块
        self.jd_parser = JDParser(llm_client)
        self.question_gen = QuestionGenerator(llm_client)
        self.evaluator = AnswerEvaluator(llm_client)
        self.report_gen = ReportGenerator(llm_client)

        # 会话状态
        self.state = InterviewState(max_follow_ups=max_follow_ups)

    # ── Public API ──────────────────────────────────────

    async def start(self, jd_text: str) -> TurnResult:
        """
        初始化面试。
        解析 JD → 生成题目 → 输出暖场介绍。

        Args:
            jd_text: 招聘 JD 全文
        """
        self.state = InterviewState(
            jd_text=jd_text,
            max_follow_ups=self.max_follow_ups,
        )

        # 1. 解析 JD
        self.state.phase = InterviewPhase.INIT
        self.state.jd_analysis = await self.jd_parser.parse(jd_text)

        # 2. 生成题目
        self.state.plan = await self.question_gen.generate(
            self.state.jd_analysis,
            total_questions=self.total_questions,
        )

        # 3. 暖场
        self.state.phase = InterviewPhase.WARMUP
        warmup = await self._generate_warmup()
        self.state.interview_started = True

        return TurnResult(
            phase=InterviewPhase.WARMUP,
            message=warmup,
            progress=f"共 {self.state.total_questions} 题",
            is_finished=False,
        )

    async def next_question(self) -> TurnResult:
        """
        进入下一题。
        第一次调用时返回第一题，之后依次返回下一题。
        如果所有题目已答完，进入结束阶段。
        """
        self.state.current_question_index += 1

        if self.state.current_question_index >= self.state.total_questions:
            # 所有题目已答完 → 生成报告
            self.state.phase = InterviewPhase.CONCLUSION
            report = await self.report_gen.generate(
                jd=self.state.jd_analysis,
                answers=self.state.answers,
            )
            return TurnResult(
                phase=InterviewPhase.CONCLUSION,
                message=self._format_report_summary(report),
                report=report,
                progress="面试结束",
                is_finished=True,
            )

        self.state.current_follow_up_count = 0
        self.state.phase = InterviewPhase.QUESTION

        question = self.state.plan.questions[self.state.current_question_index]
        self.state.current_question = question

        type_labels = {
            QuestionType.TECHNICAL: "🔧 技术基础",
            QuestionType.SCENARIO: "🏗️ 场景设计",
            QuestionType.PROJECT: "📂 项目深挖",
            QuestionType.BEHAVIORAL: "💬 行为面试",
            QuestionType.CODING: "💻 代码实操",
        }
        q_type = type_labels.get(question.type, "📝")

        display = (
            f"{q_type} | {question.category} | 难度 {'⭐' * question.difficulty}\n\n"
            f"{question.question}"
        )

        return TurnResult(
            phase=InterviewPhase.QUESTION,
            message=display,
            question=question,
            progress=self.state.progress,
            is_finished=False,
        )

    async def submit_answer(self, answer: str) -> TurnResult:
        """
        提交当前题目的回答，返回评估结果和可能的追问/下一题。

        Args:
            answer: 面试者的回答文本
        """
        if self.state.phase == InterviewPhase.CONCLUSION:
            return TurnResult(
                phase=InterviewPhase.CONCLUSION,
                message="面试已结束",
                is_finished=True,
            )

        question = self.state.current_question
        if not question:
            return await self.next_question()

        # 评估
        self.state.phase = InterviewPhase.EVALUATE
        evaluation = await self.evaluator.evaluate(question, answer)

        # 记录
        self.state.answers.append({
            "question": question,
            "answer": answer,
            "evaluation": evaluation,
            "is_follow_up": self.state.current_follow_up_count > 0,
        })

        # 判断是否需要追问
        should_follow_up = (
            evaluation.follow_up_decision != FollowUpDecision.MOVE_ON
            and self.state.current_follow_up_count < self.max_follow_ups
        )

        if should_follow_up:
            self.state.current_follow_up_count += 1
            self.state.phase = InterviewPhase.FOLLOW_UP

            return TurnResult(
                phase=InterviewPhase.FOLLOW_UP,
                message=evaluation.follow_up_question,
                question=question,  # 同一个题目的追问
                evaluation=evaluation,
                progress=f"{self.state.progress} (追问 {self.state.current_follow_up_count}/{self.state.max_follow_ups})",
                is_finished=False,
            )

        # 不追问 → 直接反馈 + 下一题
        self.state.phase = InterviewPhase.NEXT_QUESTION
        feedback = self._format_feedback(evaluation)

        # 获取下一题
        next_turn = await self.next_question()

        return TurnResult(
            phase=InterviewPhase.NEXT_QUESTION,
            message=f"{feedback}\n\n{next_turn.message}",
            question=next_turn.question,
            evaluation=evaluation,
            progress=next_turn.progress,
            is_finished=next_turn.is_finished,
            report=next_turn.report,
        )

    async def skip_question(self) -> TurnResult:
        """跳过当前题目"""
        if self.state.current_question:
            self.state.answers.append({
                "question": self.state.current_question,
                "answer": "（面试者选择跳过）",
                "evaluation": None,
                "is_follow_up": False,
            })

        return await self.next_question()

    async def run_full_interview(
        self,
        jd_text: str,
        answers: list[str],
    ) -> InterviewReport:
        """
        一次性运行完整面试（用于批量测试/演示）。

        Args:
            jd_text: JD 文本
            answers: 预置的回答列表（长度需匹配题目数）

        Returns:
            InterviewReport: 最终评估报告
        """
        await self.start(jd_text)
        turn = await self.next_question()

        answer_idx = 0
        while not turn.is_finished:
            if answer_idx < len(answers):
                answer = answers[answer_idx]
                answer_idx += 1
            else:
                answer = "（无回答）"

            turn = await self.submit_answer(answer)

        return turn.report or InterviewReport()

    # ── 工具方法 ────────────────────────────────────────

    def report_text(self) -> str:
        """获取当前报告的文本表示"""
        if not self.state.answers:
            return "尚未开始面试"
        return self._format_report_summary(None)  # 简化返回

    def reset(self) -> None:
        """重置面试会话"""
        self.state = InterviewState(max_follow_ups=self.max_follow_ups)

    # ── 状态序列化（Web 断点恢复）───────────────────────

    def to_dict(self) -> dict:
        """
        序列化完整面试状态，支持服务重启后从磁盘恢复。

        Returns:
            JSON 可序列化的状态快照
        """
        return _jsonable({
            "total_questions": self.total_questions,
            "max_follow_ups": self.max_follow_ups,
            "state": self.state,
        })

    @classmethod
    def from_dict(cls, data: dict, llm_client: LLMClient) -> Interviewer:
        """
        从状态快照重建 Interviewer（服务重启恢复）。

        Args:
            data: to_dict() 生成的快照
            llm_client: LLM 客户端

        Returns:
            恢复到快照时刻的 Interviewer
        """
        interviewer = cls(
            llm_client=llm_client,
            total_questions=data.get("total_questions", 8),
            max_follow_ups=data.get("max_follow_ups", 3),
        )

        s = data.get("state", {})
        state = interviewer.state

        # JDAnalysis
        state.jd_text = s.get("jd_text", "")
        state.jd_analysis = JDAnalysis(**s.get("jd_analysis", {}))

        # InterviewPlan → InterviewQuestion 列表
        plan_data = s.get("plan", {})
        questions = []
        for q in plan_data.get("questions", []):
            questions.append(InterviewQuestion(
                id=q.get("id", ""),
                type=QuestionType(q.get("type", "technical")),
                category=q.get("category", ""),
                question=q.get("question", ""),
                expected_points=q.get("expected_points", []),
                difficulty=q.get("difficulty", 3),
                follow_up_hints=q.get("follow_up_hints", []),
                time_limit=q.get("time_limit", 0),
                source=q.get("source", ""),
            ))
        state.plan = InterviewPlan(
            questions=questions,
            total_duration=plan_data.get("total_duration", 30),
        )

        # 当前进度
        state.current_question_index = s.get("current_question_index", -1)
        state.current_follow_up_count = s.get("current_follow_up_count", 0)
        state.max_follow_ups = s.get("max_follow_ups", 3)
        state.phase = InterviewPhase(s.get("phase", "init"))
        state.interview_started = s.get("interview_started", False)

        # 当前题目
        cur_q = s.get("current_question")
        if cur_q:
            state.current_question = InterviewQuestion(
                id=cur_q.get("id", ""),
                type=QuestionType(cur_q.get("type", "technical")),
                category=cur_q.get("category", ""),
                question=cur_q.get("question", ""),
                expected_points=cur_q.get("expected_points", []),
                difficulty=cur_q.get("difficulty", 3),
                follow_up_hints=cur_q.get("follow_up_hints", []),
                source=cur_q.get("source", ""),
            )

        # 答题记录（含评估）
        for a in s.get("answers", []):
            # 重建题目对象（快照里存的是完整题目，不是索引）
            q_data = a.get("question") or {}
            question = InterviewQuestion(
                id=q_data.get("id", ""),
                type=QuestionType(q_data.get("type", "technical")),
                category=q_data.get("category", ""),
                question=q_data.get("question", ""),
                expected_points=q_data.get("expected_points", []),
                difficulty=q_data.get("difficulty", 3),
                follow_up_hints=q_data.get("follow_up_hints", []),
                source=q_data.get("source", ""),
            )

            evaluation = None
            ev = a.get("evaluation")
            if ev:
                evaluation = EvaluationResult(
                    correctness=ev.get("correctness", 5),
                    depth=ev.get("depth", 5),
                    structure=ev.get("structure", 5),
                    relevance=ev.get("relevance", 5),
                    overall_comment=ev.get("overall_comment", ""),
                    strengths=ev.get("strengths", []),
                    weaknesses=ev.get("weaknesses", []),
                    follow_up_decision=FollowUpDecision(ev.get("follow_up_decision", "move_on")),
                    follow_up_question=ev.get("follow_up_question", ""),
                    follow_up_reason=ev.get("follow_up_reason", ""),
                    keyword_match_rate=ev.get("keyword_match_rate", 0.0),
                    matched_points=ev.get("matched_points", []),
                    missed_points=ev.get("missed_points", []),
                )
            state.answers.append({
                "question": question,
                "answer": a.get("answer", ""),
                "evaluation": evaluation,
                "is_follow_up": a.get("is_follow_up", False),
            })

        return interviewer

    # ── Private ─────────────────────────────────────────

    async def _generate_warmup(self) -> str:
        """生成暖场开场白"""
        jd = self.state.jd_analysis
        prompt = WARMUP_PROMPT.format(
            position=jd.position or "该岗位",
            skills=", ".join(jd.all_skills[:6]) or "相关技能",
        )

        response = await self.llm.chat_with_retry(
            messages=[Message(role=Role.USER, content=prompt)],
            temperature=0.7,
            max_tokens=300,
        )
        return response.content.strip()

    def _format_feedback(self, evaluation: EvaluationResult) -> str:
        """格式化单题反馈"""
        if not evaluation:
            return ""

        return (
            f"📊 本题得分: {evaluation.total_score}/10 ({evaluation.level})\n"
            f"💬 {evaluation.overall_comment}\n"
            + (f"👍 亮点: {'; '.join(evaluation.strengths)}\n" if evaluation.strengths else "")
            + (f"⚠️ 建议: {'; '.join(evaluation.weaknesses)}" if evaluation.weaknesses else "")
        )

    def _format_report_summary(self, report: InterviewReport | None) -> str:
        """格式化报告摘要文字"""
        if report and report.overall_score > 0:
            return (
                f"🎉 面试结束！\n\n"
                f"📊 总评: {report.overall_score}/10 ({report.overall_level})\n"
                f"📝 题目数: {report.total_questions}\n\n"
                f"**主要优势**:\n{chr(10).join(f'- {s}' for s in report.main_strengths)}\n\n"
                f"**待提升**:\n{chr(10).join(f'- {w}' for w in report.main_weaknesses)}\n\n"
                f"**改进建议**:\n{report.improvement_advice}"
            )
        else:
            return "✅ 面试结束！感谢你的参与。"
