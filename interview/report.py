"""
面试报告生成器
==============
汇总所有题目的评分，生成最终的面试评估报告。

报告包含:
1. 综合评分 & 等级
2. 各维度分项得分（正确性、深度、结构、相关性）
3. 逐题分析
4. 主要优势 & 待提升
5. 针对性改进建议
6. 面试结论（通过/待定/不通过）
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from core.llm import LLMClient, Message, Role

from .jd_parser import JDAnalysis
from .question_bank import InterviewQuestion


@dataclass
class InterviewReport:
    """面试评估报告"""
    # 总评
    overall_score: float = 0.0
    overall_level: str = ""

    # 分维度平均分
    avg_correctness: float = 0.0
    avg_depth: float = 0.0
    avg_structure: float = 0.0
    avg_relevance: float = 0.0

    # 题目数
    total_questions: int = 0
    answered_questions: int = 0
    follow_up_count: int = 0

    # 分析
    main_strengths: list[str] = field(default_factory=list)
    main_weaknesses: list[str] = field(default_factory=list)
    improvement_advice: str = ""

    # 结论
    verdict: str = ""      # "推荐通过" / "待定" / "不推荐通过"
    verdict_reason: str = ""

    # 逐题详情
    details: list[dict] = field(default_factory=list)


FINAL_REPORT_PROMPT = """你是一位资深面试官，请根据以下面试记录，生成一份专业的面试评估报告。

## 岗位信息
- 岗位: {position}
- 核心要求: {skills}

## 面试记录
{interview_log}

## 要求
请以 JSON 格式返回面试报告，包含以下内容:

1. **overall_score**: 综合评分 (1-10)，根据所有题目的加权表现
2. **overall_level**: 等级评定（卓越/优秀/良好/一般/需提升）
3. **main_strengths**: 面试者的 3 个主要优势
4. **main_weaknesses**: 面试者最需要提升的 3 个方面
5. **improvement_advice**: 具体的改进建议（200 字左右）
6. **verdict**: 面试结论（推荐通过 / 建议待定 / 不推荐通过）
7. **verdict_reason**: 结论理由（100 字左右）

## 输出格式
```json
{{
  "overall_score": 7.5,
  "overall_level": "优秀",
  "main_strengths": ["优势1", "优势2", "优势3"],
  "main_weaknesses": ["不足1", "不足2", "不足3"],
  "improvement_advice": "具体的改进建议...",
  "verdict": "推荐通过",
  "verdict_reason": "结论理由..."
}}
```"""


# 流式报告叙事 prompt — 纯文本输出（非 JSON），逐字流式显示
STREAM_REPORT_PROMPT = """你是一位资深面试官。请根据以下面试记录，撰写一段针对性的面试复盘建议。

## 岗位信息
- 岗位: {position}
- 核心要求: {skills}

## 面试记录
{interview_log}

## 要求
请用自然流畅的中文写一段「改进建议 + 结论理由」，不要用 JSON、不要标题、不要列表符号：
1. 先给 2-3 条具体、可执行的改进建议（紧扣面试记录中暴露的短板）
2. 再用一句话给出面试结论的理由

字数控制在 200 字左右，直接输出正文。"""


class ReportGenerator:
    """
    面试报告生成器。

    使用:
        report_gen = ReportGenerator(llm_client)
        report = await report_gen.generate(jd_analysis, answers_record)
    """

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def generate(
        self,
        jd: JDAnalysis,
        answers: list[dict],
    ) -> InterviewReport:
        """
        生成完整的面试评估报告（非流式，LLM 一次返回 JSON）。

        Args:
            jd: JD 分析结果
            answers: 包含 {question, answer, evaluation} 的记录列表

        Returns:
            InterviewReport: 结构化评估报告
        """
        report = self._compute_stats(answers)
        if not answers:
            return report

        # ── 调用 LLM 生成综合报告 ──
        interview_log = self._format_interview_log(answers)

        prompt = FINAL_REPORT_PROMPT.format(
            position=jd.position or "未知岗位",
            skills=", ".join(jd.all_skills[:8]) or "相关技能",
            interview_log=interview_log,
        )

        try:
            response = await self.llm.chat_with_retry(
                messages=[Message(role=Role.USER, content=prompt)],
                temperature=0.4,
                max_tokens=3000,
            )
            llm_report = self._parse_llm_report(response.content)
        except Exception:
            llm_report = {}

        report.main_strengths = llm_report.get("main_strengths", ["基础扎实", "表达清晰", "学习能力强"])
        report.main_weaknesses = llm_report.get("main_weaknesses", ["深度有待提升", "架构经验不足"])
        report.improvement_advice = llm_report.get("improvement_advice", "建议多参与实际项目，加深技术理解深度。")
        report.verdict = llm_report.get("verdict", "建议待定")
        report.verdict_reason = llm_report.get("verdict_reason", "综合表现尚可，建议进一步考察。")

        return report

    async def generate_stream(
        self,
        jd: JDAnalysis,
        answers: list[dict],
    ) -> AsyncIterator[dict]:
        """
        流式生成面试报告（SSE 用）。

        事件序列:
            1. {"type": "stats", "report": InterviewReport}  — 确定性统计部分（分/等级/逐题）
            2. {"type": "delta", "text": str}               — LLM 叙事逐字增量（改进建议+结论理由）
            3. {"type": "done",  "report": InterviewReport} — 完整报告（含流式叙事）

        设计: 结构化字段（分数/等级/优劣势/结论）确定性计算，立即可渲染；
        改进建议这类长文本由 LLM 流式生成（`stream_chat_with_retry` 走计数的流式入口），
        逐字推给前端 — 评估 JSON 保持整块，报告文字逐字显示。
        """
        report = self._compute_stats(answers)
        yield {"type": "stats", "report": report}

        if not answers:
            yield {"type": "done", "report": report}
            return

        # 结论 + 优劣势: 确定性聚合（可复现），不依赖 LLM
        report.verdict = self._verdict_from_score(report.overall_score)
        report.main_strengths = self._aggregate_evals(
            answers, "strengths", ["基础扎实", "表达清晰", "学习能力强"]
        )
        report.main_weaknesses = self._aggregate_evals(
            answers, "weaknesses", ["深度有待提升", "架构经验不足"]
        )
        report.verdict_reason = f"综合得分 {report.overall_score}/10，判定为「{report.verdict}」。"

        # 流式叙事: 改进建议 + 结论理由（纯文本）
        interview_log = self._format_interview_log(answers)
        prompt = STREAM_REPORT_PROMPT.format(
            position=jd.position or "未知岗位",
            skills=", ".join(jd.all_skills[:8]) or "相关技能",
            interview_log=interview_log,
        )

        try:
            chunks: list[str] = []
            async for chunk in self.llm.stream_chat_with_retry(
                messages=[Message(role=Role.USER, content=prompt)],
                temperature=0.4,
                max_tokens=1000,
            ):
                chunks.append(chunk)
                yield {"type": "delta", "text": chunk}
            narrative = "".join(chunks).strip()
            if narrative:
                report.improvement_advice = narrative
        except Exception:
            # 流式失败 → 降级到默认建议（结构化报告不受影响）
            if not report.improvement_advice:
                report.improvement_advice = "建议针对薄弱知识点做专题复习，多积累实际项目中的问题解决经验。"

        yield {"type": "done", "report": report}

    def _compute_stats(self, answers: list[dict]) -> InterviewReport:
        """确定性统计: 分维度平均分、总评、等级、逐题详情（0 API 调用）"""
        if not answers:
            return InterviewReport(
                overall_score=0,
                overall_level="无记录",
                improvement_advice="无面试记录，无法评估",
            )

        valid_evals = [
            a["evaluation"] for a in answers
            if a.get("evaluation") is not None
        ]

        report = InterviewReport()
        report.total_questions = len(answers)
        report.answered_questions = sum(1 for a in answers if a.get("answer") and a["answer"] != "（面试者选择跳过）")
        report.follow_up_count = sum(1 for a in answers if a.get("is_follow_up"))

        if valid_evals:
            report.avg_correctness = round(sum(e.correctness for e in valid_evals) / len(valid_evals), 1)
            report.avg_depth = round(sum(e.depth for e in valid_evals) / len(valid_evals), 1)
            report.avg_structure = round(sum(e.structure for e in valid_evals) / len(valid_evals), 1)
            report.avg_relevance = round(sum(e.relevance for e in valid_evals) / len(valid_evals), 1)
            report.overall_score = round(sum(e.total_score for e in valid_evals) / len(valid_evals), 1)

        # 等级评定
        s = report.overall_score
        if s >= 9:
            report.overall_level = "🌟 卓越"
        elif s >= 7.5:
            report.overall_level = "✅ 优秀"
        elif s >= 6:
            report.overall_level = "👍 良好"
        elif s >= 4:
            report.overall_level = "⚠️ 一般"
        else:
            report.overall_level = "❌ 需提升"

        report.details = self._build_details(answers)
        return report

    @staticmethod
    def _verdict_from_score(score: float) -> str:
        """确定性结论: 分数阈值映射（与 Mock 同口径，保证 mock/真实两条路径一致）"""
        if score >= 7.5:
            return "推荐通过"
        if score >= 5:
            return "建议待定"
        return "不推荐通过"

    @staticmethod
    def _aggregate_evals(answers: list[dict], field: str, default: list[str]) -> list[str]:
        """聚合各题评估的 strengths/weaknesses，按出现频次取前 3"""
        counter: Counter = Counter()
        for a in answers:
            ev = a.get("evaluation")
            if ev is None:
                continue
            for item in getattr(ev, field, None) or []:
                counter[item] += 1
        top = [item for item, _ in counter.most_common(3)]
        return top or default

    def quick_report(
        self,
        answers: list[dict],
        jd: JDAnalysis,
    ) -> InterviewReport:
        """
        快速生成统计报告（不调用 LLM）。
        适合离线/低延迟场景。
        """
        report = InterviewReport()
        valid_evals = [a["evaluation"] for a in answers if a.get("evaluation")]

        if not valid_evals:
            return report

        report.total_questions = len(answers)
        report.answered_questions = len(valid_evals)
        report.avg_correctness = round(sum(e.correctness for e in valid_evals) / len(valid_evals), 1)
        report.avg_depth = round(sum(e.depth for e in valid_evals) / len(valid_evals), 1)
        report.avg_structure = round(sum(e.structure for e in valid_evals) / len(valid_evals), 1)
        report.avg_relevance = round(sum(e.relevance for e in valid_evals) / len(valid_evals), 1)
        report.overall_score = round(sum(e.total_score for e in valid_evals) / len(valid_evals), 1)
        report.details = self._build_details(answers)

        return report

    # ── Private ─────────────────────────────────────────

    def _format_interview_log(self, answers: list[dict]) -> str:
        """格式化面试记录为文本"""
        lines = []
        for i, record in enumerate(answers):
            q = record.get("question")
            answer = record.get("answer", "")
            ev = record.get("evaluation")

            if isinstance(q, InterviewQuestion):
                q_text = q.question
            else:
                q_text = str(q)

            lines.append(f"## 第{i + 1}题: {q_text}")
            lines.append(f"回答: {answer[:500]}")
            if ev:
                lines.append(f"评分: {ev.total_score}/10 | {ev.overall_comment}")
            lines.append("")

        return "\n".join(lines)

    def _parse_llm_report(self, content: str) -> dict:
        """解析 LLM 返回的报告 JSON"""
        text = content.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:]) if "```" not in lines[0] else "\n".join(lines[1:-1])

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}

    def _build_details(self, answers: list[dict]) -> list[dict]:
        """构建逐题详情"""
        details = []
        for i, record in enumerate(answers):
            q = record.get("question")
            ev = record.get("evaluation")
            detail = {
                "index": i + 1,
                "question": q.question if isinstance(q, InterviewQuestion) else str(q),
                "answer_preview": record.get("answer", "")[:200],
                "score": ev.total_score if ev else 0,
                "level": ev.level if ev else "未评分",
                "comment": ev.overall_comment if ev else "",
            }
            details.append(detail)
        return details
