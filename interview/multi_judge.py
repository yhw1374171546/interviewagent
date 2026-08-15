"""
多评委仲裁评估
==============
解决真实评测发现的「单一 LLM 评估器对高分档系统性低估」（人工均分 8.6 vs LLM 6.4，
MAE 2.17）——单个评委有评价偏差。用多评委 + 分歧仲裁压缩偏差与随机性。

设计（轻量化，不搞完整辩论）:
    回答 → 评委 A（temperature 0.3，严格视角）
         → 评委 B（temperature 0.6，宽容视角）     [并行执行，省延迟]
         ↓
    深度/结构分歧 ≤ 1 分 → 取平均（多数情况，2 次调用）
    深度/结构分歧 > 1 分 → 仲裁 Agent 裁决          [分歧大才 +1 调用]
         ↓
    最终分数 + 分歧标记（可观测: "分歧大"提示答案有争议）

用法:
    from interview.multi_judge import MultiJudge
    judge = MultiJudge(llm)
    depth, structure, data, meta = await judge.evaluate(question, answer, ...)
    # meta = {"disagreement": "low"|"high", "judge_a_score": 6, "judge_b_score": 8, "arbitrated": bool}
"""

from __future__ import annotations

import asyncio
import json

from .prompts import active_prompt
from .question_bank import InterviewQuestion

# 仲裁 Agent 的 prompt — 结合两位评委的理由给出裁决
# 文本见 interview/prompts.py 的 "arbiter"（版本化注册表）

# 深度/结构 语义 → 分数映射（与 evaluator 保持一致）
_DEPTH_MAP = {"表面": 3, "较浅": 5, "适中": 6, "深入": 8, "非常深入": 9}
_STRUCT_MAP = {"混乱": 2, "松散": 4, "一般": 5, "清晰": 7, "优秀": 9}


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _score(data: dict) -> tuple[int, int]:
    """从 LLM 评估 JSON 提取 (depth, structure) 分数"""
    depth = _DEPTH_MAP.get(data.get("depth_level", "适中"), 6)
    struct = _STRUCT_MAP.get(data.get("structure_level", "一般"), 5)
    return depth, struct


def _format_prompt(question: InterviewQuestion, answer: str,
                   history_context: str, memory_hints: list[str] | None) -> str:
    """构造单评委的评估 prompt（与 _llm_deep_eval 同源）"""
    prompt = active_prompt("deep_eval").format(
        question=question.question,
        expected_points=", ".join(question.expected_points or ["无"]),
        answer=answer[:2500],
    )
    if history_context:
        prompt += (
            f"\n\n## 面试历史（前几轮表现）\n{history_context}\n\n"
            "请结合历史表现: 候选人之前答得好的点可以少问，"
            "之前暴露的弱点要重点追问验证。"
        )
    if memory_hints:
        prompt += (
            f"\n\n## 历史弱项（跨会话记忆）\n{'; '.join(memory_hints)}\n\n"
            "如果本题涉及这些方向，请在追问中重点验证候选人是否补足了短板。"
        )
    return prompt


class MultiJudge:
    """
    多评委仲裁评估器。

    Args:
        llm: LLM 客户端（评委 A/B + 仲裁共用）
        disagreement_threshold: 深度或结构分歧超过此值触发仲裁（默认 1）
        temperature_a / temperature_b: 两位评委的温度（严格/宽容视角）
    """

    def __init__(
        self,
        llm,
        disagreement_threshold: int = 1,
        temperature_a: float = 0.3,
        temperature_b: float = 0.6,
    ):
        self.llm = llm
        self.threshold = disagreement_threshold
        self.temperature_a = temperature_a
        self.temperature_b = temperature_b

    async def _run_judge(
        self, prompt: str, temperature: float, max_tokens: int = 2000,
    ) -> dict:
        """单个评委评估，返回解析后的 JSON（失败返回 {}）"""
        from core.llm import Message, Role

        try:
            response = await self.llm.chat_with_retry(
                messages=[Message(role=Role.USER, content=prompt)],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return _parse_json(response.content)
        except Exception:
            return {}

    async def evaluate(
        self,
        question: InterviewQuestion,
        answer: str,
        history_context: str = "",
        memory_hints: list[str] | None = None,
    ) -> tuple[int, int, dict, dict]:
        """
        多评委评估。

        Returns:
            (depth, structure, llm_data, meta)
            - llm_data: 最终采用的评估数据（仲裁结果或评委 A 的）
            - meta: 分歧信息 {"disagreement": "low"|"high", "judge_a_depth":..,
                    "judge_b_depth":.., "arbitrated": bool}
        """
        prompt = _format_prompt(question, answer, history_context, memory_hints)

        # 双评委并行（严格 + 宽容视角）
        judge_a, judge_b = await asyncio.gather(
            self._run_judge(prompt, self.temperature_a),
            self._run_judge(prompt, self.temperature_b),
        )

        meta = {
            "disagreement": "low",
            "judge_a_depth": _score(judge_a)[0] if judge_a else 0,
            "judge_b_depth": _score(judge_b)[0] if judge_b else 0,
            "judge_a_structure": _score(judge_a)[1] if judge_a else 0,
            "judge_b_structure": _score(judge_b)[1] if judge_b else 0,
            "arbitrated": False,
        }

        # 任一评委失败 → 降级为另一个评委的结果（不中断）
        if not judge_a and not judge_b:
            return 5, 5, {}, meta
        if not judge_a:
            return _score(judge_b) + (judge_b, meta)
        if not judge_b:
            return _score(judge_a) + (judge_a, meta)

        depth_a, struct_a = _score(judge_a)
        depth_b, struct_b = _score(judge_b)

        # 分歧判定: 深度或结构任一超过阈值 → 仲裁
        if abs(depth_a - depth_b) > self.threshold or abs(struct_a - struct_b) > self.threshold:
            meta["disagreement"] = "high"
            arbiter_data = await self._run_arbiter(
                question, answer, judge_a, judge_b,
            )
            if arbiter_data:
                meta["arbitrated"] = True
                return _score(arbiter_data) + (arbiter_data, meta)
            # 仲裁失败 → 取两位评委的平均分（不中断）
            avg_depth = round((depth_a + depth_b) / 2)
            avg_struct = round((struct_a + struct_b) / 2)
            merged = dict(judge_b)
            merged["overall_comment"] = (
                f"（多评委分歧，仲裁失败，取均分）{judge_b.get('overall_comment', '')}"
            )
            return avg_depth, avg_struct, merged, meta

        # 分歧小 → 取评委 B（宽容视角偏用户友好）的完整数据，深度/结构取平均
        avg_depth = round((depth_a + depth_b) / 2)
        avg_struct = round((struct_a + struct_b) / 2)
        merged = dict(judge_b)
        merged["overall_comment"] = (
            f"（多评委一致）{judge_b.get('overall_comment', '')}"
        )
        return avg_depth, avg_struct, merged, meta

    async def _run_arbiter(
        self,
        question: InterviewQuestion,
        answer: str,
        judge_a: dict,
        judge_b: dict,
    ) -> dict:
        """仲裁 Agent 裁决分歧"""
        from core.llm import Message, Role

        prompt = active_prompt("arbiter").format(
            question=question.question,
            expected_points=", ".join(question.expected_points or ["无"]),
            answer=answer[:2000],
            judge_a=json.dumps(judge_a, ensure_ascii=False, indent=1)[:1500],
            judge_b=json.dumps(judge_b, ensure_ascii=False, indent=1)[:1500],
        )
        try:
            response = await self.llm.chat_with_retry(
                messages=[Message(role=Role.USER, content=prompt)],
                temperature=0.2,
                max_tokens=1500,
            )
            data = _parse_json(response.content)
            # 校验仲裁输出结构完整（含 depth_level / structure_level）
            if data and "depth_level" in data and "structure_level" in data:
                return data
            return {}
        except Exception:
            return {}
