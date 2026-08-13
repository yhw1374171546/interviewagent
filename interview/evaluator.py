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

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

from core.llm import LLMClient, Message, Role

from .question_bank import InterviewQuestion

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
LLM_DEEP_EVAL_PROMPT = """你是一位资深面试官。请分析面试者对以下问题的回答质量。

## 题目
{question}

## 期望回答要点
{expected_points}

## 面试者回答
{answer}

## 分析要求

1. **深度分析**: 回答是停留在表面还是深入了原理？举例: 如果问 GIL，只说"全局解释器锁"是表面，讲清楚为什么设计 GIL、什么时候是瓶颈、怎么绕过，才算有深度。

2. **结构分析**: 回答是否有逻辑层次？（总分总？先结论后展开？还是想到哪说到哪？）

3. **追问决策**:
   - 回答很短/太浅 → "deepen"
   - 有明显错误或漏洞 → "challenge"，生成一个具体的挑战性问题
   - 回答很好 → "upgrade"，出一个更难的相关问题
   - 过于抽象没有例子 → "example"
   - 回答充分 → "move_on"

4. **评价**: 用一句话总结，同时指出一个亮点和一个不足。

## 输出格式
```json
{{
  "depth_level": "表面|较浅|适中|深入|非常深入",
  "structure_level": "混乱|松散|一般|清晰|优秀",
  "overall_comment": "一句话评价",
  "strengths": ["亮点"],
  "weaknesses": ["不足"],
  "follow_up_decision": "deepen|challenge|upgrade|example|move_on",
  "follow_up_question": "追问内容(move_on 时为空)",
  "follow_up_reason": "追问原因"
}}
```"""


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

    def __init__(self, llm_client: LLMClient | None = None):
        """
        Args:
            llm_client: LLM 客户端。为 None 时仅使用关键词匹配。
        """
        self.llm = llm_client

    async def evaluate(
        self,
        question: InterviewQuestion,
        answer: str,
    ) -> EvaluationResult:
        """
        评估回答 — 带完整边界防护。

        确定性拦截链（全部 0 API 调用，按顺序短路）:
            空回答 → 超短 → 重复字符垃圾 → 复读题目 → ...
        之后才是 关键词匹配 + LLM 深度评估（含降级兜底）。
        """
        # ── 边界 1: 空回答 ──
        if not answer.strip():
            return EvaluationResult(
                correctness=1, depth=1, structure=1, relevance=1,
                overall_comment="面试者未回答此题",
                matched_points=[], missed_points=question.expected_points or [],
                keyword_match_rate=0.0,
            )

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

        # ── 阶段 2: LLM 深度评估 ──
        if self.llm:
            depth, structure, llm_data = await self._llm_deep_eval(question, answer)
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

        # ── 边界 D1: 追问文本为空 → 按决策类型给默认话术 ──
        follow_up_question = (llm_data.get("follow_up_question") or "").strip()
        if decision != FollowUpDecision.MOVE_ON and not follow_up_question:
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
        )

    # ── 边界检测辅助 ────────────────────────────────────

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
            # 拆词: "GIL定义" → ["gil", "定义"]
            keywords = self._tokenize(point)
            # 只要命中 ≥50% 的关键词就算匹配
            hits = sum(1 for kw in keywords if kw in answer_lower)
            if hits >= max(1, len(keywords) * 0.5):
                matched.append(point)
            else:
                missed.append(point)

        rate = len(matched) / len(expected_points) if expected_points else 1.0
        return matched, missed, rate

    def _tokenize(self, text: str) -> list[str]:
        """中英文混合分词"""
        # 简单策略: 英文按空格+标点分，中文按字组合
        tokens = re.findall(r"[a-zA-Z0-9]+|[一-鿿]+", text.lower())
        return [t.strip() for t in tokens if len(t.strip()) > 1]

    def _relevance_match(self, answer: str, question: str) -> float:
        """评估回答与问题的相关性（基于问题中关键词在回答中的出现率）"""
        q_keywords = self._tokenize(question.lower())
        if not q_keywords:
            return 0.8
        hits = sum(1 for kw in q_keywords if kw.lower() in answer.lower())
        return hits / len(q_keywords)

    def _rate_from_match(self, rate: float, min_score: int, max_score: int) -> int:
        """将匹配率映射到分数区间"""
        score = min_score + rate * (max_score - min_score)
        return round(self._clamp(score, min_score, max_score))

    # ── LLM 深度评估 ────────────────────────────────────

    async def _llm_deep_eval(
        self,
        question: InterviewQuestion,
        answer: str,
    ) -> tuple[int, int, dict]:
        """LLM 深度评估（只评估机器做不了的）"""
        prompt = LLM_DEEP_EVAL_PROMPT.format(
            question=question.question,
            expected_points=", ".join(question.expected_points or ["无"]),
            answer=answer[:2500],
        )

        try:
            response = await self.llm.chat(
                messages=[Message(role=Role.USER, content=prompt)],
                temperature=0.3,
                max_tokens=800,
            )

            data = self._parse_json(response.content)

            # 将语义评级映射为分数
            depth_map = {"表面": 3, "较浅": 5, "适中": 6, "深入": 8, "非常深入": 9}
            struct_map = {"混乱": 2, "松散": 4, "一般": 5, "清晰": 7, "优秀": 9}

            depth_score = depth_map.get(data.get("depth_level", "适中"), 6)
            struct_score = struct_map.get(data.get("structure_level", "一般"), 5)

            return depth_score, struct_score, data

        except Exception:
            return 5, 5, {}

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
