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

import asyncio
import dataclasses
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum

from config import settings
from core.llm import LLMClient, Message, Role
from utils.logger import get_logger

from .evaluator import AnswerEvaluator, EvaluationResult, FollowUpDecision
from .follow_up_agent import FollowUpAgent
from .jd_parser import JDAnalysis, JDParser
from .memory_context import (
    InterviewMemory,
    MemoryEntry,
    build_history_summary,
    remember_answer_async,
)
from .question_bank import InterviewQuestion, QuestionType
from .question_gen import InterviewPlan, QuestionGenerator
from .report import InterviewReport, ReportGenerator

logger = get_logger(__name__)

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
    cache_hit: bool = False  # JD 语义缓存是否命中（可观测性）

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

    # 跨会话记忆: 历史弱项提示（面试开始时检索，注入评估上下文）
    memory_hints: list[str] = field(default_factory=list)

    # 自适应难度（D2）: 按已答表现动态调整题目难度
    adaptive_enabled: bool = False
    adaptive_candidates: dict[str, list] = field(default_factory=dict)  # qtype → 候选池
    adaptive_used_ids: set[str] = field(default_factory=set)            # 已用/已替换题目
    adaptive_adjustments: list[dict] = field(default_factory=list)      # 留痕 [{index, from, to, reason}]

    # 各阶段耗时（秒，性能指标）: jd_parse / question_gen / warmup /
    # evaluate / report — evaluate 为累计值，evaluate_count 为次数
    timings: dict[str, float] = field(default_factory=dict)
    evaluate_count: int = 0

    # 调用级指标（可观测性）: 每阶段 {latency, prompt_tokens, completion_tokens, model}
    metrics: dict[str, dict] = field(default_factory=dict)

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
        memory: InterviewMemory | None = None,
        llm_strong: LLMClient | None = None,
        defer_report: bool = False,
        adaptive_enabled: bool = False,
        cost_budget=None,
        jd_cache=None,
        multi_judge=None,
        calibrate: bool = False,
    ):
        self.llm = llm_client
        self.total_questions = total_questions
        self.max_follow_ups = max_follow_ups
        # 延迟报告: True 时面试结束不再内联生成报告，改由 stream_report() 流式生成
        # （Web SSE 路径用；CLI/测试默认 False，保持原内联报告行为）
        self.defer_report = defer_report
        # 自适应难度: True 时按已答表现动态调整题目难度（D2 特性）
        self.adaptive_enabled = adaptive_enabled
        # 成本预算控制（#5）: 超 warn 阈值评估降级省 token，超 hard 阈值强制终止。
        # 不传时用默认宽松预算（真实单场 ≈¥0.02，默认 ¥0.5 兜底，正常不触发）
        from .cost_control import CostBudget

        self.cost_budget = cost_budget if cost_budget is not None else CostBudget()
        # JD 语义缓存（B2）: 相似 JD 复用解析结果，省 LLM 调用。
        # 默认 None = 关闭（避免离线路径加载 embedding 模型触网卡住）；
        # 需要时显式传入 JDSemanticCache（如 Web 生产环境）
        self.jd_cache = jd_cache
        # 多评委仲裁（A1 能力接入）: 双评委并行 + 分歧仲裁，解决单评委评分偏差。
        # 默认 None = 单评委（向后兼容）；Web 生产传入 MultiJudge
        self.multi_judge = multi_judge
        # 评分校准（A2 能力接入）: 按「关键词命中率 vs LLM 评分」纠正高低估
        # （评测 MAE 1.2→0.76）。默认 False 向后兼容；Web 生产启用
        self.calibrate_enabled = calibrate

        # 跨会话记忆（可选 — ChromaDB 不可用时自动降级进程内存储）
        self.memory = memory if memory is not None else InterviewMemory()
        # 会话 ID（Web 层在会话创建后回填，用于记忆元数据标记）
        self.session_id = ""

        # 模型路由: 高频调用（评估/JD/出题/暖场）用快模型 llm_client，
        # 最终报告用强模型 llm_strong（未配置时回退主模型）
        self.llm_strong = llm_strong or llm_client

        # 子模块
        self.jd_parser = JDParser(llm_client)
        self.question_gen = QuestionGenerator(llm_client)
        self.evaluator = AnswerEvaluator(
            llm_client, multi_judge=multi_judge, calibrate=calibrate,
        )
        self.report_gen = ReportGenerator(self.llm_strong)
        # 追问自主决策 Agent（快模型）— 追问环节的"大脑"，失败时回退评估器 5 分类
        self.follow_up_agent = FollowUpAgent(llm_client)

        # 会话状态
        self.state = InterviewState(max_follow_ups=max_follow_ups)

    # ── 可观测性辅助 ─────────────────────────────────────

    def _llm_snapshot(self, client: LLMClient) -> dict:
        """取某个 LLM 客户端的累计用量快照"""
        stats = getattr(client, "usage_stats", None)
        return dict(stats) if stats else {}

    def _record_stage(
        self,
        stage: str,
        t0: float,
        client: LLMClient,
        before: dict,
    ) -> None:
        """记录一个阶段的延迟与 token 消耗（增量）"""
        after = self._llm_snapshot(client)
        self.state.metrics[stage] = {
            "latency": round(time.perf_counter() - t0, 2),
            "prompt_tokens": after.get("prompt_tokens", 0) - before.get("prompt_tokens", 0),
            "completion_tokens": after.get("completion_tokens", 0) - before.get("completion_tokens", 0),
            "model": getattr(client, "model", ""),
        }
        self.state.timings[stage] = round(time.perf_counter() - t0, 2)

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

        # 1. 解析 JD（带语义缓存: 相似 JD 复用结果，省 LLM 调用）
        self.state.phase = InterviewPhase.INIT
        t0 = time.perf_counter()
        snap_before = self._llm_snapshot(self.llm)

        cached_analysis = None
        if self.jd_cache is not None:
            cached_analysis = self.jd_cache.lookup(jd_text)

        if cached_analysis is not None:
            # 语义命中缓存 → 0 LLM 调用
            self.state.jd_analysis = cached_analysis
            self.state.cache_hit = True
        else:
            self.state.jd_analysis = await self.jd_parser.parse(jd_text)
            self.state.cache_hit = False
            if self.jd_cache is not None:
                self.jd_cache.store(jd_text, self.state.jd_analysis)

        self._record_stage("jd_parse", t0, self.llm, snap_before)

        # 1.5 跨会话记忆: 检索与当前 JD 技能相关的历史弱项
        #      （无历史或 ChromaDB 不可用时返回空列表，不影响主流程）
        self.state.memory_hints = self.memory.recall_weaknesses(
            self.state.jd_analysis.all_skills
        )

        # 2 + 3. 生成题目与暖场并行（互不依赖，各含一次 LLM 调用 —
        #        串行会多等一个推理模型的延迟，实测可省 8-15s）
        self.state.phase = InterviewPhase.WARMUP
        t0 = time.perf_counter()
        snap_before = self._llm_snapshot(self.llm)
        plan_task = asyncio.create_task(self.question_gen.generate(
            self.state.jd_analysis,
            total_questions=self.total_questions,
        ))
        warmup_task = asyncio.create_task(self._generate_warmup())
        self.state.plan, warmup = await asyncio.gather(plan_task, warmup_task)
        self._record_stage("question_gen+warmup", t0, self.llm, snap_before)
        self.state.interview_started = True

        # 自适应难度: 构建各类型候选池（同类型 + 同技能方向），供后续替换
        if self.adaptive_enabled:
            self._init_adaptive_pool()

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
            if self.defer_report:
                # 报告由 stream_report() 流式生成（Web SSE 路径）
                return TurnResult(
                    phase=InterviewPhase.CONCLUSION,
                    message="面试结束，正在生成报告…",
                    report=None,
                    progress="面试结束",
                    is_finished=True,
                )
            t0 = time.perf_counter()
            snap_before = self._llm_snapshot(self.llm_strong)
            report = await self.report_gen.generate(
                jd=self.state.jd_analysis,
                answers=self.state.answers,
            )
            self._record_stage("report", t0, self.llm_strong, snap_before)
            # 性能指标日志（面试复盘/成本观测用）
            logger.info(
                f"面试阶段耗时: {self.state.timings} | "
                f"评估 {self.state.evaluate_count} 次, 平均 "
                f"{self.state.timings.get('evaluate', 0) / max(1, self.state.evaluate_count):.1f}s"
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

        # ── 自适应难度（D2）: 按已答表现调整当前题难度 ──
        if self.adaptive_enabled:
            question = self._apply_adaptive_difficulty(question)

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

    # ── 自适应难度（D2）──────────────────────────────────

    def _init_adaptive_pool(self) -> None:
        """构建各题型候选池: 同类型 + 与 JD 技能有标签重叠的题库题"""
        from .adaptive import build_candidate_pool
        from .question_bank import QUESTION_BANK

        skills = (
            self.state.jd_analysis.all_skills
            + self.state.jd_analysis.soft_skills
            + self.state.jd_analysis.interview_focus
        )
        pool: dict[QuestionType, list] = {}
        for q in QUESTION_BANK:
            pool.setdefault(q.type, []).append(q)
        self.state.adaptive_candidates = {
            str(qtype): build_candidate_pool(bank, qtype, skills)
            for qtype, bank in pool.items()
        }

    def _apply_adaptive_difficulty(
        self,
        question,
    ) -> InterviewQuestion:
        """
        按已答表现调整当前题难度（确定性规则）。

        已答记录 → 平均分 → 目标难度 → 同类型候选里找替换题。
        找不到合适替换 → 保持原题（不折腾，面试不中断）。
        替换留痕到 adaptive_adjustments（可观测/面试叙事用）。
        """
        from .adaptive import compute_target_difficulty, pick_replacement

        scores = [a.get("evaluation").total_score for a in self.state.answers
                  if a.get("evaluation") is not None]
        if not scores:
            return question  # 第一题不调整

        target = compute_target_difficulty(scores, question.difficulty)
        if target == question.difficulty:
            return question

        candidates = self.state.adaptive_candidates.get(str(question.type), [])
        exclude = self.state.adaptive_used_ids | {q.id for q in self.state.plan.questions}
        replacement = pick_replacement(
            question, target, candidates, exclude_ids=exclude,
        )
        if replacement is None:
            return question

        from .question_bank import InterviewQuestion

        new_q = InterviewQuestion(
            id=replacement.id,
            type=replacement.type,
            category=replacement.category,
            question=replacement.question,
            expected_points=replacement.expected_points,
            difficulty=replacement.difficulty,
            follow_up_hints=replacement.follow_up_hints,
            source="adaptive",
            code=replacement.code,
            tags=replacement.tags,
        )
        self.state.adaptive_used_ids.add(new_q.id)
        self.state.adaptive_adjustments.append({
            "index": self.state.current_question_index + 1,
            "from_difficulty": question.difficulty,
            "to_difficulty": new_q.difficulty,
            "from_id": question.id,
            "to_id": new_q.id,
            "reason": f"已答平均 {sum(scores) / len(scores):.1f} 分",
        })
        return new_q

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

        # 成本预算控制（#5）: 超 warn 阈值 → 本次评估降级为纯规则（省 LLM 调用）
        budget_status = self.cost_budget.check()
        degrade_eval = budget_status in ("warn", "hard")

        # 评估 — 注入轮内记忆（前几轮摘要）与跨会话记忆（历史弱项）
        # 使追问能"翻旧账"、对历史短板重点验证
        self.state.phase = InterviewPhase.EVALUATE
        history_context = build_history_summary(self.state.answers)
        # 上下文预算守卫（Context 管理）: 历史+弱项超预算时按优先级裁剪，
        # 保当前题/回答（CRITICAL），弱项次之（HIGH），历史摘要可裁剪（MEDIUM）
        from .context_budget import fit_eval_context

        history_context, memory_hints = fit_eval_context(
            history_context,
            self.state.memory_hints,
            question_len=len(question.question),
            answer_len=len(answer),
        )
        t0 = time.perf_counter()
        snap_before = self._llm_snapshot(self.llm)
        evaluator = (
            self.evaluator if not degrade_eval
            else self._rule_only_evaluator()
        )
        evaluation = await evaluator.evaluate(
            question,
            answer,
            history_context=history_context,
            memory_hints=memory_hints,
        )
        self.state.timings["evaluate"] = round(
            self.state.timings.get("evaluate", 0) + time.perf_counter() - t0, 2,
        )
        # 评估阶段指标为多次调用累计
        after = self._llm_snapshot(self.llm)
        prev = self.state.metrics.get("evaluate", {
            "latency": 0, "prompt_tokens": 0, "completion_tokens": 0,
        })
        self.state.metrics["evaluate"] = {
            "latency": round(prev["latency"] + time.perf_counter() - t0, 2),
            "prompt_tokens": prev["prompt_tokens"] + after.get("prompt_tokens", 0) - snap_before.get("prompt_tokens", 0),
            "completion_tokens": prev["completion_tokens"] + after.get("completion_tokens", 0) - snap_before.get("completion_tokens", 0),
            "model": getattr(self.llm, "model", ""),
        }
        self.state.evaluate_count += 1

        # 成本预算: 记录本次评估用量（增量）
        snap_after = self._llm_snapshot(self.llm)
        self.cost_budget.record(
            prompt_tokens=snap_after.get("prompt_tokens", 0) - snap_before.get("prompt_tokens", 0),
            completion_tokens=snap_after.get("completion_tokens", 0) - snap_before.get("completion_tokens", 0),
            model=getattr(self.llm, "model", ""),
        )

        # 成本预算: 超 hard 阈值 → 强制终止（防成本失控）
        if budget_status == "hard" or self.cost_budget.check() == "hard":
            logger.warning(
                f"[CostBudget] 超出成本硬上限 "
                f"({self.cost_budget.summary()['total_cost_yuan']} 元)，强制结束面试"
            )
            self.state.phase = InterviewPhase.CONCLUSION
            return TurnResult(
                phase=InterviewPhase.CONCLUSION,
                message=(
                    "本场面试因成本预算限制提前结束。"
                    f"已消耗 {self.cost_budget.total_tokens} tokens"
                    f"（≈¥{self.cost_budget.total_cost:.3f}）。"
                ),
                report=None,
                progress="面试结束（预算限制）",
                is_finished=True,
            )

        # 记录
        self.state.answers.append({
            "question": question,
            "answer": answer,
            "evaluation": evaluation,
            "is_follow_up": self.state.current_follow_up_count > 0,
        })

        # 写入跨会话记忆（异步、容错、不阻塞面试主流程）
        # 技能标签用题目类别近似（InterviewQuestion 无 tags 字段）
        remember_answer_async(self.memory, MemoryEntry(
            question=question.question,
            answer=answer,
            score=evaluation.total_score,
            category=question.category,
            question_type=question.type.value,
            skills=[question.category],
            session_id=self.session_id,
        ))

        # 追问决策（混合 Agent）：
        # 1. 确定性边界短路（超短/垃圾/复读）→ 评估器已明确判断需追问，直接保留（不覆盖）
        # 2. 正常评估 → FollowUpAgent 自主决策；Agent 不可用 → 回退评估器 5 分类
        boundary_reasons = ("回答过短", "检测到无效输入", "检测到题目复读")
        if evaluation.follow_up_reason in boundary_reasons:
            should_follow_up = (
                evaluation.follow_up_decision != FollowUpDecision.MOVE_ON
                and self.state.current_follow_up_count < self.max_follow_ups
            )
            follow_up_q = evaluation.follow_up_question
        else:
            decision = await self.follow_up_agent.decide(
                question, answer, evaluation,
                asked_follow_ups=self._asked_follow_ups(question),
            )
            if decision["continue_follow_up"] is None:
                # Agent 不可用 → 回退评估器 5 分类（不中断面试）
                should_follow_up = (
                    evaluation.follow_up_decision != FollowUpDecision.MOVE_ON
                    and self.state.current_follow_up_count < self.max_follow_ups
                )
                follow_up_q = evaluation.follow_up_question
            elif decision["continue_follow_up"] and self.state.current_follow_up_count < self.max_follow_ups:
                should_follow_up = True
                follow_up_q = decision["question"] or evaluation.follow_up_question
            else:
                should_follow_up = False
                follow_up_q = ""

        if should_follow_up:
            self.state.current_follow_up_count += 1
            self.state.phase = InterviewPhase.FOLLOW_UP

            return TurnResult(
                phase=InterviewPhase.FOLLOW_UP,
                message=follow_up_q,
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

    async def stream_report(self) -> AsyncIterator[dict]:
        """
        流式生成最终评估报告（Web SSE 路径）。

        事件序列（由 ReportGenerator.generate_stream 产出）:
            {"type": "stats", "report": ...} / {"type": "delta", "text": ...} / {"type": "done", "report": ...}

        流式结束后记录 report 阶段指标（延迟/token 经 stream_chat_with_retry 已计数）
        并将状态机置为 CONCLUSION。
        """
        t0 = time.perf_counter()
        snap_before = self._llm_snapshot(self.llm_strong)
        async for event in self.report_gen.generate_stream(
            jd=self.state.jd_analysis,
            answers=self.state.answers,
        ):
            if event["type"] == "done":
                # 在产出 done 前记录 report 阶段指标（token 已在流式入口累计），
                # 保证 done 之后的 metrics 卡片能看到报告阶段的 token 成本
                self._record_stage("report", t0, self.llm_strong, snap_before)
                self.state.phase = InterviewPhase.CONCLUSION
            yield event

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

    def _rule_only_evaluator(self):
        """纯规则评估器（成本降级用）: 无 LLM，零成本，关键词兜底评分"""
        from .evaluator import AnswerEvaluator

        if getattr(self, "_rule_evaluator", None) is None:
            self._rule_evaluator = AnswerEvaluator(None)
        return self._rule_evaluator

    def report_text(self) -> str:
        """获取当前报告的文本表示"""
        if not self.state.answers:
            return "尚未开始面试"
        return self._format_report_summary(None)  # 简化返回

    def reset(self) -> None:
        """重置面试会话"""
        self.state = InterviewState(max_follow_ups=self.max_follow_ups)

    def session_cost_estimate(self) -> dict:
        """
        本场面试的 token 汇总与成本估算（可观测性）。

        价格表来自 settings.llm_pricing（元/百万 token），
        可按模型分别计价（模型路由下 flash 与 pro 成本差异显著）。
        """
        total_in = 0
        total_out = 0
        per_model: dict[str, list[int]] = {}
        for m in self.state.metrics.values():
            pin = m.get("prompt_tokens", 0)
            pout = m.get("completion_tokens", 0)
            total_in += pin
            total_out += pout
            model = m.get("model", "")
            per_model.setdefault(model, [0, 0])
            per_model[model][0] += pin
            per_model[model][1] += pout

        cost = 0.0
        for model, (pin, pout) in per_model.items():
            price = settings.llm_pricing.get(model, [0, 0])
            cost += pin / 1_000_000 * price[0] + pout / 1_000_000 * price[1]

        return {
            "prompt_tokens": total_in,
            "completion_tokens": total_out,
            "cost_yuan": round(cost, 4),
        }

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
            "defer_report": self.defer_report,
            "state": self.state,
        })

    @classmethod
    def from_dict(
        cls,
        data: dict,
        llm_client: LLMClient,
        memory: InterviewMemory | None = None,
        llm_strong: LLMClient | None = None,
    ) -> Interviewer:
        """
        从状态快照重建 Interviewer（服务重启恢复）。

        Args:
            data: to_dict() 生成的快照
            llm_client: LLM 客户端（快模型，高频调用）
            memory: 跨会话记忆实例（不参与序列化，由调用方注入）
            llm_strong: 强模型（最终报告），未传时回退 llm_client

        Returns:
            恢复到快照时刻的 Interviewer
        """
        interviewer = cls(
            llm_client=llm_client,
            total_questions=data.get("total_questions", 8),
            max_follow_ups=data.get("max_follow_ups", 3),
            memory=memory,
            llm_strong=llm_strong,
            defer_report=data.get("defer_report", False),
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
                code=q.get("code"),
                tags=q.get("tags", []),
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
        state.memory_hints = s.get("memory_hints", [])
        state.timings = s.get("timings", {})
        state.metrics = s.get("metrics", {})
        state.evaluate_count = s.get("evaluate_count", 0)

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
                code=cur_q.get("code"),
                tags=cur_q.get("tags", []),
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
                code=q_data.get("code"),
                tags=q_data.get("tags", []),
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
                    code_judge=ev.get("code_judge"),
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
            max_tokens=600,
        )
        return response.content.strip()

    def _asked_follow_ups(self, question: InterviewQuestion) -> list[str]:
        """收集本题历史已追问的问题（排除当前这条），供 Agent 避免重复追问"""
        qs = []
        for a in self.state.answers[:-1]:
            if a.get("question") != question:
                continue
            ev = a.get("evaluation")
            if ev and ev.follow_up_question:
                qs.append(ev.follow_up_question)
        return qs

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
