"""
答案评估器 (v2 — 双引擎)
==========================
阶段 1: 关键词命中率计算（确定性，0 API 调用）
阶段 2: LLM 深度评估（仅对语义模糊的部分，如表达结构、追问决策）

为什么不用 LLM 直接评价:
    - 关键词匹配是客观的 — 答到了就是答到了
    - LLM 容易对回答"看起来不错"的给高分（hallucination-like bias）
    - 代码题直接用 code_judge 跑测试，不需要 LLM 评价

评估流程:
    用户回答
       │
       ├──→ 关键词匹配引擎 → 正确性/相关性的基础分
       │    比较: 回答中出现了多少个 expected_points 中的关键词
       │
       ├──→ (如果是代码题) → CodeJudge 真实运行 → pass/fail 事实
       │
       └──→ LLM 语义评估 → 深度/结构的分数 + 追问决策
            只传回答，不让 LLM 自己打分（减少 bias）
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

from core.llm import LLMClient, Message, Role
from utils.logger import get_logger

from .output_validator import repair_truncated_json
from .prompts import active_prompt
from .question_bank import InterviewQuestion, QuestionType

logger = get_logger(__name__)

# ── 边界防护工具 ───────────────────────────────────────────────

def _safe_decision(value: str | None) -> FollowUpDecision:
    """
    LLM 返回的追问决策安全转换。

    非法值（如 LLM 幻觉出 "skip"）回退 MOVE_ON，绝不抛 ValueError —
    真实 LLM 模式下枚举解析崩溃会让整场面试挂掉。
    """
    if value is None:
        return FollowUpDecision.MOVE_ON
    try:
        return FollowUpDecision(value)
    except ValueError:
        return FollowUpDecision.MOVE_ON


def _default_follow_up(decision: FollowUpDecision) -> str:
    """按追问决策类型给默认话术（LLM 返回空追问文本时兜底）"""
    defaults = {
        FollowUpDecision.DEEPEN: "能展开说说吗？具体是怎么实现的？",
        FollowUpDecision.CHALLENGE: "这个方案如果遇到更极端的场景，还能 work 吗？",
        FollowUpDecision.EXAMPLE: "能举个你实际项目中的例子吗？",
        FollowUpDecision.UPGRADE: "回答得不错，那更高阶的问题是：这个方案有什么局限性？",
    }
    return defaults.get(decision, "")


class FollowUpDecision(str, Enum):
    DEEPEN = "deepen"
    CHALLENGE = "challenge"
    UPGRADE = "upgrade"
    EXAMPLE = "example"
    MOVE_ON = "move_on"


@dataclass
class EvaluationResult:
    """单次回答的评估结果"""
    correctness: int = 5
    depth: int = 5
    structure: int = 5
    relevance: int = 5

    overall_comment: str = ""
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)

    follow_up_decision: FollowUpDecision = FollowUpDecision.MOVE_ON
    follow_up_question: str = ""
    follow_up_reason: str = ""

    # 关键词匹配详情（可解释性）
    keyword_match_rate: float = 0.0
    matched_points: list[str] = field(default_factory=list)
    missed_points: list[str] = field(default_factory=list)

    # 编程题判题结果（非编程题为 None）:
    # {"passed", "total_tests", "passed_tests", "failed_tests", "errors", "details"}
    code_judge: dict | None = None

    # 评估可解释性（P0 思维链）:
    # analysis — LLM 结构化逐步分析（prompt 输出格式的 analysis 字段）
    # reasoning_text — DeepSeek 推理模型的原始思维链（reasoning_content，
    #   仅单评委路径保存；多评委路径合并保存在 meta，此处取最终采用评委的）
    analysis: str = ""
    reasoning_text: str = ""

    @property
    def total_score(self) -> float:
        return round(
            self.correctness * 0.35
            + self.depth * 0.25
            + self.structure * 0.20
            + self.relevance * 0.20,
            1,
        )

    @property
    def level(self) -> str:
        s = self.total_score
        if s >= 9:
            return "🌟 卓越"
        elif s >= 7.5:
            return "✅ 优秀"
        elif s >= 6:
            return "👍 良好"
        elif s >= 4:
            return "⚠️ 一般"
        else:
            return "❌ 需提升"


# LLM 深度评估 prompt — 不直接让 LLM 打分，让它分析回答质量
# 文本见 interview/prompts.py 的 "deep_eval"（版本化注册表）


# LLM 代码评审 prompt — 无自动判题用例的代码题（类设计/SQL/Shell 等）降级路径
# 文本见 interview/prompts.py 的 "code_review"（版本化注册表）


class AnswerEvaluator:
    """
    答案评估器 — 双引擎模式。

    关键词匹配(确定性) + LLM 语义分析(补充)。

    使用:
        evaluator = AnswerEvaluator(llm_client)
        result = await evaluator.evaluate(question, answer)
        print(f"关键词命中率: {result.keyword_match_rate:.0%}")
        print(f"综合得分: {result.total_score}/10")
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        multi_judge=None,
        calibrate: bool = False,
    ):
        """
        Args:
            llm_client: LLM 客户端。为 None 时仅使用关键词匹配。
            multi_judge: 多评委仲裁器（可选）。传入后 LLM 深度评估走
                「双评委并行 + 分歧仲裁」（解决单评委评分偏差/高分低估）。
                为 None 时保持单评委（默认，不改变既有行为）。
            calibrate: 评分校准（可选）。True 时按「关键词命中率 vs LLM 评分」
                校准分数（纠正高分低估/低分高估）。默认 False 不改变既有行为。
        """
        self.llm = llm_client
        self.multi_judge = multi_judge
        self.calibrate_enabled = calibrate

    async def evaluate(
        self,
        question: InterviewQuestion,
        answer: str,
        history_context: str = "",
        memory_hints: list[str] | None = None,
    ) -> EvaluationResult:
        """
        评估回答 — 带完整边界防护。

        确定性拦截链（全部 0 API 调用，按顺序短路）:
            空回答 → 超短 → 重复字符垃圾 → 复读题目 → ...
        之后才是 关键词匹配 + LLM 深度评估（含降级兜底）。

        Args:
            question: 当前题目
            answer: 面试者回答
            history_context: 前几轮问答摘要（轮内记忆）— 注入 LLM 深度评估，
                使追问能引用面试者之前的回答（"翻旧账"能力）
            memory_hints: 跨会话记忆检索出的历史弱项 — 注入同样的上下文，
                让 LLM 对候选人之前的薄弱方向重点追问
        """
        # ── 边界 1: 空回答 ──
        if not answer.strip():
            return EvaluationResult(
                correctness=1, depth=1, structure=1, relevance=1,
                overall_comment="面试者未回答此题",
                matched_points=[], missed_points=question.expected_points or [],
                keyword_match_rate=0.0,
            )

        # ── 边界 1.5: Prompt 注入（操纵评分/越狱/泄露提示词等）──
        # 面试者回答夹带指令试图操纵 AI → 确定性拦截，不浪费 LLM 调用
        from .injection import detect_injection

        injection = detect_injection(answer)
        if injection["detected"]:
            return EvaluationResult(
                correctness=1, depth=1, structure=1, relevance=1,
                overall_comment=(
                    f"⚠️ 检测到 Prompt 注入（{injection['category']}: "
                    f"「{injection['pattern']}」），回答不作数并视为无效"
                ),
                weaknesses=[f"检测到 Prompt 注入（{injection['category']}）"],
                follow_up_decision=FollowUpDecision.DEEPEN,
                follow_up_question=(
                    "你的回答疑似包含操纵系统/评分的内容，请回到题目本身，"
                    "用自己的话回答这道题。"
                ),
                follow_up_reason="检测到 Prompt 注入",
                keyword_match_rate=0.0,
                matched_points=[],
                missed_points=question.expected_points or [],
            )

        # ── 编程题 → 沙箱判题（真实执行测试用例，不适用自然语言的关键词/复读检测）──
        if question.type == QuestionType.CODING and question.code:
            return await self._evaluate_code(question, answer)

        # ── 边界 2: 超短回答 ──
        if len(answer) < 20:
            return EvaluationResult(
                correctness=3, depth=2, structure=2, relevance=3,
                overall_comment="回答过于简短，缺乏细节",
                weaknesses=["回答过于简短"],
                follow_up_decision=FollowUpDecision.DEEPEN,
                follow_up_question="能展开说说吗？具体是怎么实现的？",
                follow_up_reason="回答过短",
            )

        # ── 边界 3: 垃圾输入 — 纯重复字符（如 "GOGOGOGO..."）─
        # 信息密度极低，在确定性层拦截，不浪费一次 LLM 调用
        unique_ratio = len(set(answer.lower())) / len(answer)
        if len(answer) >= 20 and unique_ratio < 0.15:
            return EvaluationResult(
                correctness=1, depth=1, structure=1, relevance=1,
                overall_comment="回答内容无效（重复字符），未触及题目要点",
                weaknesses=["回答无效"],
                follow_up_decision=FollowUpDecision.DEEPEN,
                follow_up_question="请认真回答，围绕题目考查的技术点展开。",
                follow_up_reason="检测到无效输入",
                keyword_match_rate=0.0,
                matched_points=[],
                missed_points=question.expected_points or [],
            )

        # ── 边界 4: 复读题目原文 ──
        # 只抄题目不回答 → 相关性命中题面词会虚高，必须拦截
        if self._is_question_restate(question.question, answer):
            return EvaluationResult(
                correctness=2, depth=2, structure=2, relevance=3,
                overall_comment="回答基本是题目原文的复读，没有给出自己的理解",
                weaknesses=["复读题目", "无自己的理解"],
                follow_up_decision=FollowUpDecision.DEEPEN,
                follow_up_question="请用自己的话重新组织回答，说明你对这个知识点的理解。",
                follow_up_reason="检测到题目复读",
                keyword_match_rate=0.0,
                matched_points=[],
                missed_points=question.expected_points or [],
            )

        # ── 阶段 1: 关键词匹配 (确定性) ──
        matched, missed, match_rate = self._keyword_match(
            answer.lower(),
            [p.lower() for p in (question.expected_points or [])],
        )

        if match_rate is None:
            # 边界 B1: 题目缺少期望要点（LLM 补充生成的题常缺）
            # 无法客观验证 → 正确性取中性值，不虚高
            correctness = 5
            keyword_stuffing = False
        else:
            correctness = self._rate_from_match(match_rate, 3, 9)

            # ── 边界 5 (A5): 关键词堆砌 — 极短回答命中大量要点 ──
            # 只罗列关键词不加解释 → 正确性封顶，不能拿满分
            keyword_stuffing = len(answer) < 60 and match_rate >= 0.6
            if keyword_stuffing:
                correctness = min(correctness, 6)

        relevance = self._rate_from_match(
            self._relevance_match(answer, question.question), 3, 9,
        )

        # ── 阶段 2: LLM 深度评估（单评委 or 多评委仲裁）──
        reasoning_text = ""
        if self.llm:
            if self.multi_judge is not None:
                # 多评委: 双评委并行 + 分歧仲裁（解决单评委评分偏差）
                depth, structure, llm_data, judge_meta = await self.multi_judge.evaluate(
                    question, answer,
                    history_context=history_context,
                    memory_hints=memory_hints,
                )
                reasoning_text = judge_meta.get("reasoning", "")
                if judge_meta.get("disagreement") == "high":
                    llm_data = dict(llm_data)
                    llm_data["overall_comment"] = (
                        f"（多评委分歧{'已仲裁' if judge_meta.get('arbitrated') else '，取均分'}）"
                        + llm_data.get("overall_comment", "")
                    )
            else:
                depth, structure, llm_data, reasoning_text = await self._llm_deep_eval(
                    question, answer,
                    history_context=history_context,
                    memory_hints=memory_hints,
                )
        else:
            depth = self._rate_from_match(match_rate or 0.0, 2, 8)
            structure = 5
            llm_data = {}

        # ── 边界 C3/C4: LLM 评估不可用 → 明确评语，不静默 ──
        if not llm_data or not llm_data.get("overall_comment"):
            llm_data = llm_data or {}
            llm_data["overall_comment"] = "（语义评估暂不可用，按关键词命中评分）"
            llm_data.setdefault(
                "follow_up_decision",
                "deepen" if (match_rate or 0.0) < 0.4 else "move_on",
            )

        # ── 边界 C1: 追问决策枚举安全转换（非法值不崩溃）──
        decision = _safe_decision(llm_data.get("follow_up_decision"))

        # ── 边界 D1: 追问文本为空 → 上下文追问兜底 ──
        # 优先用未命中要点生成贴合题目的追问（"你的回答没有提到 X"），
        # 没有未命中要点时才回退通用话术 — 修复"乱问"问题
        follow_up_question = (llm_data.get("follow_up_question") or "").strip()
        if decision != FollowUpDecision.MOVE_ON and not follow_up_question:
            if missed:
                follow_up_question = (
                    f"你刚才的回答没有提到「{'、'.join(missed[:2])}」，"
                    "能展开说说吗？"
                )
                decision = FollowUpDecision.DEEPEN
            else:
                follow_up_question = _default_follow_up(decision)

        # ── 边界 6 (A6): 同句重复凑字数 → 结构分封顶 ──
        padded = self._is_padded_repetition(answer)
        if padded:
            structure = min(structure, 4)
            weaknesses = list(llm_data.get("weaknesses", []))
            weaknesses.append("同一内容重复多次，疑似凑字数")
            llm_data["weaknesses"] = weaknesses
            if not llm_data.get("strengths"):
                llm_data["strengths"] = []

        if keyword_stuffing:
            weaknesses = list(llm_data.get("weaknesses", []))
            weaknesses.append("只罗列了关键词，缺少展开说明")
            llm_data["weaknesses"] = weaknesses

        # ── 评分校准（C1）: 按关键词命中率纠正高分低估/低分高估 ──
        if self.calibrate_enabled:
            from .score_calibration import calibrate_score

            cal_notes = []
            correctness, cm = calibrate_score(correctness, match_rate)
            depth, dm = calibrate_score(depth, match_rate)
            structure, sm = calibrate_score(structure, match_rate)
            relevance, rm = calibrate_score(relevance, match_rate)
            for m in (cm, dm, sm, rm):
                if m["adjusted"] and m["reason"] not in cal_notes:
                    cal_notes.append(m["reason"])
            if cal_notes:
                llm_data = dict(llm_data)
                llm_data["overall_comment"] = (
                    f"（评分校准: {'；'.join(cal_notes[:2])}）"
                    + llm_data.get("overall_comment", "")
                )

        return EvaluationResult(
            correctness=correctness,
            depth=depth,
            structure=structure,
            relevance=relevance,
            overall_comment=llm_data.get("overall_comment", ""),
            strengths=llm_data.get("strengths", []),
            weaknesses=llm_data.get("weaknesses", []),
            follow_up_decision=decision,
            follow_up_question=follow_up_question,
            follow_up_reason=llm_data.get("follow_up_reason", ""),
            keyword_match_rate=match_rate if match_rate is not None else 0.5,
            matched_points=matched,
            missed_points=missed,
            analysis=llm_data.get("analysis", ""),
            reasoning_text=reasoning_text,
        )

    async def evaluate_many(
        self,
        items: list[tuple[InterviewQuestion, str]],
        max_concurrency: int = 4,
        history_context: str = "",
        memory_hints: list[str] | None = None,
    ) -> list[EvaluationResult]:
        """
        批量评估（C1 并行化）— 并发上限受控的多题评估。

        面试主流程保持逐题串行（对话式流程依赖用户逐题回答），此接口服务
        评测/批处理场景（judge_eval 30 样本评测等）：串行 30 次 → 并发 N 路，
        真实 LLM 下耗时 ≈ 串行 / N（IO 并行）。

        Args:
            items: (question, answer) 列表 — 结果顺序与输入一致（gather 保序）
            max_concurrency: 并发上限（DeepSeek API 并发过高触发 429，默认 4 安全）
            history_context / memory_hints: 所有样本共用的轮内/跨会话上下文

        注意: 多评委模式下每次 evaluate 内部还有双评委并行，外层并发 N
        实际请求并发 ≈ 2N — 默认 4 时实际 8 路，仍远低于 API 限流阈值。
        """
        sem = asyncio.Semaphore(max_concurrency)

        async def worker(question: InterviewQuestion, answer: str) -> EvaluationResult:
            async with sem:
                return await self.evaluate(
                    question, answer,
                    history_context=history_context,
                    memory_hints=memory_hints,
                )

        return await asyncio.gather(*(worker(q, a) for q, a in items))

    async def _evaluate_code(
        self,
        question: InterviewQuestion,
        answer: str,
    ) -> EvaluationResult:
        """
        编程题沙箱判题 — 真实执行测试用例，pass/fail 事实覆盖正确性。

        代码题不适用自然语言的「关键词/复读」检测，直接交给 code_judge：
        通过率决定正确性，编译/安全/超时给最低分并提示。

        无自动判题用例的代码题（类设计题/SQL/Shell 等）→ 降级为 LLM 代码评审，
        不伪造 0/0 AC 假阳性。
        """
        from .code_judge import CodeQuestion, TestCase, run_judge

        code_meta = question.code or {}
        test_case_dicts = code_meta.get("test_cases", [])
        if not test_case_dicts:
            return await self._evaluate_code_review(question, answer)

        language = code_meta.get("language", "python")
        code_question = CodeQuestion(
            id=question.id,
            title=question.category,
            description=question.question,
            function_signature=code_meta.get("function_signature", ""),
            example_input="",
            example_output="",
            test_cases=[TestCase(**tc) for tc in test_case_dicts],
        )

        try:
            judge = await run_judge(
                answer, code_question,
                language=language,
                mode=code_meta.get("mode", "core"),
            )
        except Exception as e:  # 判题器自身异常 → 不中断面试，降级为提示
            return EvaluationResult(
                correctness=3, depth=5, structure=5, relevance=5,
                overall_comment=f"判题服务异常，无法执行代码: {type(e).__name__}",
                weaknesses=["判题服务不可用"],
                follow_up_decision=FollowUpDecision.MOVE_ON,
            )

        total = max(1, judge.total_tests)
        rate = judge.passed_tests / total

        # 编译/安全/超时 → 最低分；否则正确性 = 1 + 通过率×9（1~10）
        hard_fail = judge.errors > 0 and not judge.details
        first_name = judge.details[0].get("name", "") if judge.details else ""
        if hard_fail or first_name in ("安全检查", "编译错误", "超时", "不支持的语言"):
            correctness = 1
            comment = f"❌ {first_name}: {judge.details[0].get('error', '')[:120]}"
            decision = FollowUpDecision.DEEPEN
            follow_up = "代码未能执行，请检查后重新提交。"
            strengths, weaknesses = [], ["代码无法执行"]
        elif judge.passed:
            correctness = 10
            comment = f"✅ 通过全部 {judge.passed_tests}/{judge.total_tests} 个测试用例"
            decision = FollowUpDecision.MOVE_ON
            follow_up = ""
            strengths, weaknesses = ["全部测试用例通过"], []
        else:
            correctness = round(1 + rate * 9)
            comment = (
                f"⚠️ 通过 {judge.passed_tests}/{judge.total_tests} 个测试用例，"
                f"还有 {judge.failed_tests} 个未通过"
            )
            decision = FollowUpDecision.DEEPEN
            follow_up = "有测试用例未通过，请修复后重新提交。"
            strengths, weaknesses = [], ["部分测试用例未通过"]

        return EvaluationResult(
            correctness=correctness,
            # 代码题不适用"深度/结构/相关性"维度，均取正确性值，
            # 使 total_score = correctness（四维权重和 = 1.0），
            # 即"全过 = 10 分"，避免"代码全对却只得 7 分"的困惑
            depth=correctness,
            structure=correctness,
            relevance=correctness,
            overall_comment=comment,
            strengths=strengths,
            weaknesses=weaknesses,
            follow_up_decision=decision,
            follow_up_question=follow_up,
            follow_up_reason=f"测试通过率 {judge.passed_tests}/{judge.total_tests}",
            keyword_match_rate=rate,
            matched_points=[],
            missed_points=[],
            code_judge={
                "language": language,
                "passed": judge.passed,
                "verdict": judge.verdict,
                "total_tests": judge.total_tests,
                "passed_tests": judge.passed_tests,
                "failed_tests": judge.failed_tests,
                "errors": judge.errors,
                "execution_time_ms": judge.execution_time_ms,
                "details": judge.details,
            },
        )

    # ── 边界检测辅助 ────────────────────────────────────

    async def _evaluate_code_review(
        self,
        question: InterviewQuestion,
        answer: str,
    ) -> EvaluationResult:
        """
        无自动判题用例的代码题 → LLM 代码评审。

        覆盖: 类设计题（MinStack/BSTIterator）、SQL/Shell 无 Python 模板题、
        特殊输入输出题（二叉树序列化、isBadVersion 等）。这些题没法自动生成
        测试用例，但也不能 0/0 判 AC（假阳性）——交给 LLM 评审代码质量，
        LLM 不可用时给中性分并明确说明，不伪造通过。
        """
        if not self.llm:
            return EvaluationResult(
                correctness=5, depth=5, structure=5, relevance=5,
                overall_comment="本题无自动判题用例，且语义评估不可用，按中性分处理",
                strengths=[],
                weaknesses=["无自动判题用例", "语义评估不可用"],
                follow_up_decision=FollowUpDecision.DEEPEN,
                follow_up_question="能展开讲讲你的代码设计思路和复杂度分析吗？",
                follow_up_reason="无自动判题用例",
                keyword_match_rate=0.0,
                matched_points=[],
                missed_points=[],
                code_judge={
                    "language": "python",
                    "passed": False,
                    "verdict": "REVIEW",
                    "total_tests": 0,
                    "passed_tests": 0,
                    "failed_tests": 0,
                    "errors": 0,
                    "execution_time_ms": 0,
                    "details": [],
                    "review": True,
                    "comment": "本题无自动判题用例，且语义评估不可用，按中性分处理",
                },
            )

        prompt = active_prompt("code_review").format(
            question=question.question,
            answer=answer[:2500],
        )
        try:
            response = await self.llm.chat_with_retry(
                messages=[Message(role=Role.USER, content=prompt)],
                temperature=0.3,
                max_tokens=1200,
            )
            data = self._parse_json(response.content)
            if not data:
                repaired = repair_truncated_json(response.content)
                if repaired is not None:
                    data = repaired
            if not data:
                logger.warning(f"LLM 代码评审输出不可解析，已降级: {response.content[:200]!r}")
                data = {}
        except Exception as e:
            logger.warning(f"LLM 代码评审失败（降级到中性分）: {type(e).__name__}: {e}")
            data = {}

        # 正确性解析（非法值回退中性 5 分）
        try:
            correctness = int(float(str(data.get("correctness", 5))))
        except (TypeError, ValueError):
            correctness = 5
        correctness = self._clamp(correctness, 1, 10)

        decision = _safe_decision(data.get("follow_up_decision"))
        follow_up_question = (data.get("follow_up_question") or "").strip()
        if decision != FollowUpDecision.MOVE_ON and not follow_up_question:
            follow_up_question = "能展开讲讲你的代码设计思路和复杂度分析吗？"

        comment = data.get("overall_comment") or "（代码评审未返回评价，按中性分处理）"
        strengths = data.get("strengths") or []
        weaknesses = data.get("weaknesses") or []
        if not weaknesses and decision != FollowUpDecision.MOVE_ON:
            weaknesses = ["代码有待改进"]

        return EvaluationResult(
            correctness=correctness,
            depth=correctness,
            structure=correctness,
            relevance=correctness,
            overall_comment=f"💻 AI 代码评审（无自动判题用例）: {comment}",
            strengths=strengths,
            weaknesses=weaknesses,
            follow_up_decision=decision,
            follow_up_question=follow_up_question,
            follow_up_reason=data.get("follow_up_reason") or "AI 代码评审",
            keyword_match_rate=0.0,
            matched_points=[],
            missed_points=[],
            code_judge={
                "language": "python",
                "passed": correctness >= 7,
                "verdict": "REVIEW",
                "total_tests": 0,
                "passed_tests": 0,
                "failed_tests": 0,
                "errors": 0,
                "execution_time_ms": 0,
                "details": [],
                "review": True,
                "comment": comment,
            },
        )

    @staticmethod
    def _is_question_restate(question: str, answer: str) -> bool:
        """
        检测「复读题目」: 回答中超过 70% 的词来自题目本身。
        正常回答会复用题目术语，但不会整段抄题。
        """
        q_tokens = set(re.findall(r"[a-zA-Z0-9]+|[一-鿿]{2,}", question.lower()))
        a_tokens = re.findall(r"[a-zA-Z0-9]+|[一-鿿]{2,}", answer.lower())
        if not a_tokens or not q_tokens:
            return False
        from_question = sum(1 for t in a_tokens if t in q_tokens)
        return from_question / len(a_tokens) > 0.7

    @staticmethod
    def _is_padded_repetition(answer: str, min_repeats: int = 3) -> bool:
        """
        检测「同句重复 N 遍凑字数」。
        按中英文句子切分，同一句话出现 ≥3 次视为灌水。
        """
        sentences = [s.strip() for s in re.split(r"[。！？!?.\n]+", answer) if len(s.strip()) >= 5]
        if len(sentences) < 3:
            return False
        _, top_count = Counter(sentences).most_common(1)[0]
        return top_count >= min_repeats

    # ── 关键词匹配引擎 ──────────────────────────────────

    def keyword_analysis(
        self,
        question: InterviewQuestion,
        answer: str,
    ) -> dict:
        """
        确定性关键词层分析（0 LLM 调用，秒级响应）。

        供两条路径使用:
            1. SSE 流式评估: 先推「已命中/未命中要点」给用户看（评估第一屏）
            2. 追问决策并行化: FollowUpAgent.decide 不依赖 LLM 评语即可启动

        Returns:
            {"match_rate", "matched", "missed", "relevance", "comment_hint", "boundary"}
            boundary: 命中确定性边界（空/超短/垃圾/复读）时为标记，否则 ""
        """
        a = answer.strip()
        empty = {"match_rate": 0.0, "matched": [], "relevance": 0.0,
                 "comment_hint": "未回答", "boundary": "empty"}
        if not a:
            empty["missed"] = question.expected_points or []
            return empty
        if len(a) < 20:
            return {"match_rate": 0.2, "matched": [], "relevance": 0.3,
                    "missed": question.expected_points or [],
                    "comment_hint": "回答过短", "boundary": "short"}
        if len(a) >= 20 and len(set(a.lower())) / len(a) < 0.15:
            return {"match_rate": 0.0, "matched": [], "relevance": 0.0,
                    "missed": question.expected_points or [],
                    "comment_hint": "检测到无效输入（重复字符）", "boundary": "spam"}
        if self._is_question_restate(question.question, a):
            return {"match_rate": 0.0, "matched": [], "relevance": 0.3,
                    "missed": question.expected_points or [],
                    "comment_hint": "检测到题目复读", "boundary": "restate"}

        matched, missed, rate = self._keyword_match(
            a.lower(),
            [p.lower() for p in (question.expected_points or [])],
        )
        rate = rate if rate is not None else 0.5
        relevance = self._relevance_match(a, question.question)
        hint = f"关键词命中率 {rate:.0%}"
        if missed:
            hint += f"，未命中: {'、'.join(missed[:2])}"
        else:
            hint += "，要点全覆盖"
        return {
            "match_rate": rate,
            "matched": matched,
            "missed": missed,
            "relevance": relevance,
            "comment_hint": hint,
            "boundary": "",
        }

    def _keyword_match(
        self,
        answer_lower: str,
        expected_points: list[str],
    ) -> tuple[list[str], list[str], float | None]:
        """
        检查回答中命中了多少个期望要点。

        对于每个期望要点，提取关键词后在回答中检索。
        不是简单的字符串包含，而是对每个要点拆词后做模糊匹配。
        """
        if not expected_points:
            # 边界 B1: 题目没有期望要点 → 无法验证正确性。
            # 返回 None（而不是 1.0）— 否则 evaluate() 会把
            # 命中率当 100% 处理，正确性恒 9 分虚高
            return [], [], None

        matched = []
        missed = []

        for point in expected_points:
            # 拆词: "B+树结构" → ["b+", "树结构"]
            keywords = self._tokenize(point)
            # 每词按命中程度计分（精确子串=1.0，中文按字符覆盖率），
            # 总分达标视为命中要点。单关键词要点阈值 0.6（字符覆盖率
            # 语义命中即可），多关键词按词数 × 50%
            hit_score = sum(self._keyword_hit(kw, answer_lower) for kw in keywords)
            threshold = 0.6 if len(keywords) == 1 else len(keywords) * 0.5
            if hit_score >= threshold:
                matched.append(point)
            else:
                missed.append(point)

        rate = len(matched) / len(expected_points) if expected_points else 1.0
        return matched, missed, rate

    def _tokenize(self, text: str) -> list[str]:
        """
        中英文混合分词。

        英文保留符号后缀（"b+"、"c++"），中文取连续串。
        注意: 中文连续串是"最大匹配"整串（如"范围查询优势"），
        真实回答几乎不会逐字复述 — 因此匹配时用 _keyword_hit 的
        字符覆盖率，而不是要求整串相等。
        """
        tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9+]*|[一-鿿]{2,}", text.lower())
        return [t.strip() for t in tokens if len(t.strip()) > 1]

    @staticmethod
    def _keyword_hit(keyword: str, answer_lower: str) -> float:
        """
        单个关键词的命中程度 (0.0-1.0)。

        - 精确子串命中 → 1.0
        - 中文长词 → 字符覆盖率（"范围查询优势" 在回答里出现
          "范围查询" 4/6 字符 = 0.67，语义上已命中要点）
        - 其余 → 0
        """
        if keyword in answer_lower:
            return 1.0
        # 含中文字符的词做字符覆盖匹配（限制词长 ≥4，避免"查询"这种
        # 高频双字词误命中）
        if len(keyword) >= 4 and any("一" <= ch <= "鿿" for ch in keyword):
            chars = set(keyword)
            coverage = sum(1 for ch in chars if ch in answer_lower) / len(chars)
            return coverage
        return 0.0

    def _relevance_match(self, answer: str, question: str) -> float:
        """评估回答与问题的相关性（基于问题中关键词在回答中的出现率）"""
        q_keywords = self._tokenize(question.lower())
        if not q_keywords:
            return 0.8
        answer_lower = answer.lower()
        # 同样用命中程度而非整串相等（中文题目词不会逐字复述）
        hits = sum(self._keyword_hit(kw, answer_lower) for kw in q_keywords)
        return min(1.0, hits / len(q_keywords))

    def _rate_from_match(self, rate: float, min_score: int, max_score: int) -> int:
        """将匹配率映射到分数区间"""
        score = min_score + rate * (max_score - min_score)
        return round(self._clamp(score, min_score, max_score))

    # ── LLM 深度评估 ────────────────────────────────────

    async def _llm_deep_eval(
        self,
        question: InterviewQuestion,
        answer: str,
        history_context: str = "",
        memory_hints: list[str] | None = None,
    ) -> tuple[int, int, dict, str]:
        """LLM 深度评估（只评估机器做不了的）

        Returns:
            (depth, structure, llm_data, reasoning_content)
            - llm_data: 评估 JSON（含 analysis 结构化思维链字段）
            - reasoning_content: DeepSeek 推理模型的原始思维链（可解释性）
        """
        prompt = active_prompt("deep_eval").format(
            question=question.question,
            expected_points=", ".join(question.expected_points or ["无"]),
            answer=answer[:2500],
        )

        # 记忆注入: 轮内摘要 + 跨会话弱项（均附加在模板之后，不影响原格式）
        if history_context:
            prompt += (
                f"\n\n## 面试历史（前几轮表现）\n{history_context}\n\n"
                "请结合历史表现: 候选人之前答得好的点可以少问，"
                "之前暴露的弱点要重点追问验证。"
            )
        if memory_hints:
            hint_text = "; ".join(memory_hints)
            prompt += (
                f"\n\n## 历史弱项（跨会话记忆）\n{hint_text}\n\n"
                "如果本题涉及这些方向，请在追问中重点验证候选人是否补足了短板。"
            )

        try:
            response = await self.llm.chat_with_retry(
                messages=[Message(role=Role.USER, content=prompt)],
                temperature=0.3,
                max_tokens=2000,
            )

            data = self._parse_json(response.content)

            # 解析失败 → 尝试修复被截断的 JSON（推理模型可能超长被截断）
            if not data:
                repaired = repair_truncated_json(response.content)
                if repaired is not None:
                    data = repaired

            # 将语义评级映射为分数
            depth_map = {"表面": 3, "较浅": 5, "适中": 6, "深入": 8, "非常深入": 9}
            struct_map = {"混乱": 2, "松散": 4, "一般": 5, "清晰": 7, "优秀": 9}

            depth_score = depth_map.get(data.get("depth_level", "适中"), 6)
            struct_score = struct_map.get(data.get("structure_level", "一般"), 5)

            if not data:
                # 兜底前必须留痕 — 之前静默返回 {} 导致线上问题无法定位
                logger.warning(
                    f"LLM 评估输出不可解析，已降级: {response.content[:200]!r}"
                )

            return (
                depth_score,
                struct_score,
                data,
                (response.reasoning_content or ""),
            )

        except Exception as e:
            logger.warning(f"LLM 深度评估失败（降级到规则引擎）: {type(e).__name__}: {e}")
            return 5, 5, {}, ""

    def _parse_json(self, text: str) -> dict:
        """从 LLM 输出中提取 JSON"""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _clamp(value: float, min_val: int, max_val: int) -> float:
        return max(min_val, min(max_val, value))
