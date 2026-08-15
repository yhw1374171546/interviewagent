"""
面试题目生成器 (v2 — 题库检索 + LLM 适配)
==========================================
从题库检索相关题目（确定性） → LLM 微调使题目贴合具体 JD。

流程:
    JD 技能列表
       │
       ├──→ QuestionBankRetriever: 标签匹配 → Top-K 题目
       │    返回: 最匹配的 N 道题
       │
       └──→ LLM 适配 (可选): 将题目中的占位符替换为 JD 中的具体技术
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .prompts import render_prompt
from .question_bank import (
    QUESTION_BANK,
    InterviewQuestion,
    QuestionBankRetriever,
    QuestionType,
)

if TYPE_CHECKING:
    from core.llm import LLMClient

    from .jd_parser import JDAnalysis


@dataclass
class InterviewPlan:
    """面试计划"""
    questions: list[InterviewQuestion] = field(default_factory=list)
    total_duration: int = 30

    @property
    def question_count(self) -> int:
        return len(self.questions)


# 各题型默认权重
DEFAULT_WEIGHTS = {
    QuestionType.TECHNICAL: 0.35,
    QuestionType.SCENARIO: 0.20,
    QuestionType.PROJECT: 0.20,
    QuestionType.BEHAVIORAL: 0.15,
    QuestionType.CODING: 0.10,
}


class QuestionGenerator:
    """
    题目生成器 — 题库检索 + LLM 适配。

    使用:
        gen = QuestionGenerator(llm_client)  # llm_client 可选
        plan = await gen.generate(jd_analysis, total=8)
    """

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm = llm_client
        self.retriever = QuestionBankRetriever(QUESTION_BANK)

    async def generate(self, jd: JDAnalysis, total_questions: int = 8,
                       weights: dict | None = None) -> InterviewPlan:
        """
        根据 JD 生成一套面试题。

        步骤:
            1. 题库检索 (确定性)
            2. LLM 适配 (可选)
            3. 不足部分 LLM 补充 (仅当题库覆盖不够时)
        """
        weights = weights or DEFAULT_WEIGHTS

        # ── 步骤 1: 题库检索 ──
        search_skills = jd.all_skills + jd.soft_skills + jd.interview_focus
        bank_results = self.retriever.retrieve(
            skills=search_skills,
            total=total_questions,
        )

        # 转换为 InterviewQuestion
        questions: list[InterviewQuestion] = []
        seen_ids: set[str] = set()
        for bq in bank_results:
            questions.append(InterviewQuestion(
                id=bq.id,
                type=bq.type,
                category=bq.category,
                question=bq.question,
                expected_points=bq.expected_points,
                difficulty=bq.difficulty,
                follow_up_hints=bq.follow_up_hints,
                source="bank",
                code=bq.code,
                tags=bq.tags,
            ))
            seen_ids.add(bq.id)

        # ── 步骤 2: LLM 适配 (可选) ──
        if self.llm and questions:
            questions = await self._customize_questions(questions, jd)

        # ── 步骤 3: 不够则 LLM 补充 ──
        if len(questions) < total_questions and self.llm:
            needed = total_questions - len(questions)
            extra_questions = await self._llm_generate_extra(
                jd, needed, seen_ids
            )
            questions += extra_questions

        # ── 步骤 4: 还不够则复用题库通识题 ──
        if len(questions) < total_questions:
            generic = self.retriever._fill_generic(
                total_questions - len(questions),
                {q.id for q in questions},
            )
            for bq in generic:
                questions.append(InterviewQuestion(
                    id=bq.id, type=bq.type, category=bq.category,
                    question=bq.question, expected_points=bq.expected_points,
                    difficulty=bq.difficulty, follow_up_hints=bq.follow_up_hints,
                    source="bank",
                    code=bq.code,
                    tags=bq.tags,
                ))

        # 重新编号
        type_prefix = {
            QuestionType.TECHNICAL: "T",
            QuestionType.SCENARIO: "S",
            QuestionType.PROJECT: "P",
            QuestionType.BEHAVIORAL: "B",
            QuestionType.CODING: "C",
        }
        counts: dict[QuestionType, int] = {}
        for q in questions:
            counts[q.type] = counts.get(q.type, 0) + 1
            q.id = f"{type_prefix.get(q.type, 'Q')}{counts[q.type]}"

        return InterviewPlan(
            questions=questions[:total_questions],
            total_duration=total_questions * 5,
        )

    async def _customize_questions(
        self,
        questions: list[InterviewQuestion],
        jd: JDAnalysis,
    ) -> list[InterviewQuestion]:
        """LLM 微调题目，使其贴合具体 JD"""
        from core.llm import Message, Role

        if not questions:
            return questions

        q_list = "\n\n---\n\n".join(
            f"[{q.id}] ({q.category}) {q.question}" for q in questions[:5]
        )

        prompt = render_prompt(
            "question_customize",
            position=jd.position,
            skills=", ".join(jd.all_skills[:8]),
            responsibilities=", ".join(jd.responsibilities[:3]) or "未指定",
            q_list=q_list,
        )
        try:
            response = await self.llm.chat_with_retry(
                messages=[Message(role=Role.USER, content=prompt)],
                temperature=0.5,
                max_tokens=3000,
            )

            data = self._parse_json(response.content)
            if isinstance(data, list):
                id_map = {item.get("id", ""): item.get("question", "") for item in data}
                for q in questions:
                    if q.id in id_map and id_map[q.id]:
                        q.question = id_map[q.id]
                        q.source = "bank+customized"

        except Exception:
            pass

        return questions

    async def _llm_generate_extra(
        self,
        jd: JDAnalysis,
        needed: int,
        exclude_ids: set[str],
    ) -> list[InterviewQuestion]:
        """LLM 补充题库未覆盖的题目"""
        from core.llm import Message, Role

        prompt = render_prompt(
            "question_generate",
            needed=needed,
            position=jd.position,
            skills=", ".join(jd.all_skills[:10]),
            focus=", ".join(jd.interview_focus),
        )

        try:
            response = await self.llm.chat_with_retry(
                messages=[Message(role=Role.USER, content=prompt)],
                temperature=0.7,
                max_tokens=3000,
            )

            data = self._parse_json(response.content)
            if not isinstance(data, list):
                return []

            questions = []
            for item in data:
                try:
                    q_type = QuestionType(item.get("type", "technical"))
                except ValueError:
                    q_type = QuestionType.TECHNICAL

                questions.append(InterviewQuestion(
                    id="LLM",
                    type=q_type,
                    category=item.get("category", "综合"),
                    question=item.get("question", ""),
                    expected_points=item.get("expected_points", []),
                    difficulty=max(1, min(5, item.get("difficulty", 3))),
                    source="llm",
                ))

            return questions

        except Exception:
            return []

    def _parse_json(self, text: str):
        """清理 markdown 包装后解析 JSON"""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:]) if "```" not in lines[0] else "\n".join(lines[1:-1])

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
