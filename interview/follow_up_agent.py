"""
追问自主决策 Agent
==================
面试追问环节的「大脑」。

此前追问是硬编码的 5 分类（deepen/challenge/upgrade/example/move_on），
由评估器 JSON 里的 follow_up_decision 决定。本模块把它升级为「混合 Agent」：

    - 状态机管骨架（出题/收答/评分这些确定性步骤）
    - FollowUpAgent 管追问自由度 —— 让 LLM 根据「题目 + 回答 + 评估 + 历史追问」
      自主决定：是否继续追问、追什么、何时停

设计原则:
    1. 追问上限（max_follow_ups）保留为安全网，防无限循环
    2. LLM 失败 / JSON 非法 → 返回 continue_follow_up=None，由调用方
       回退到评估器的 5 分类兜底（绝不因 Agent 挂了让面试中断）
    3. 追问问题要「贴题」——注入未命中要点，让 LLM 围绕真实短板追问
"""

from __future__ import annotations

import json

from core.llm import LLMClient, Message, Role

from .evaluator import EvaluationResult
from .question_bank import InterviewQuestion

FOLLOW_UP_AGENT_PROMPT = """你是一位资深面试官，正在对候选人进行追问。请根据以下信息，自主判断是否继续追问，以及追问什么。

## 当前题目
{question}

## 候选人的回答
{answer}

## 评估参考
- 关键词命中率: {match_rate:.0%}
- 评语: {comment}
- 已命中要点: {matched}
- 未命中要点: {missed}

## 本轮已追问过的问题（不要重复）
{asked_follow_ups}

## 你的任务
1. **判断**：这个回答是否已经考察得足够充分？还有没有值得深挖的价值点（如回答有漏洞、未覆盖关键要点、可以挑战其方案、可以要求举实际例子）？
2. 若还有价值点 → 生成一个**具体、贴题**的追问（优先围绕「未命中要点」）
3. 若已充分 → 停止追问

## 输出格式
```json
{{
  "continue": true,
  "question": "追问内容",
  "reason": "追问理由"
}}
```"""


class FollowUpAgent:
    """
    追问自主决策 Agent。

    使用:
        agent = FollowUpAgent(llm_client)
        decision = await agent.decide(question, answer, evaluation, asked)
        # decision = {"continue_follow_up": bool|None, "question": str, "reason": str}
    """

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def decide(
        self,
        question: InterviewQuestion,
        answer: str,
        evaluation: EvaluationResult,
        asked_follow_ups: list[str] | None = None,
    ) -> dict:
        """
        自主决定是否追问 + 追问什么。

        Returns:
            {"continue_follow_up": bool|None, "question": str, "reason": str}
            - continue_follow_up=None 表示 Agent 不可用（LLM 失败/JSON 非法），
              调用方应回退到评估器的 5 分类兜底。
        """
        asked = asked_follow_ups or []

        # Prompt 注入防护: 回答夹带操纵指令 → 不进入 LLM 追问决策，
        # 直接返回"提示回到题目"的追问（省一次调用 + 防操纵传播）
        from .injection import detect_injection

        injection = detect_injection(answer)
        if injection["detected"]:
            return {
                "continue_follow_up": True,
                "question": (
                    "你的回答疑似包含操纵系统/评分的内容，请回到题目本身，"
                    "用自己的话回答这道题。"
                ),
                "reason": f"检测到 Prompt 注入（{injection['category']}）",
            }

        prompt = FOLLOW_UP_AGENT_PROMPT.format(
            question=question.question,
            answer=answer[:2500],
            match_rate=evaluation.keyword_match_rate or 0.0,
            comment=evaluation.overall_comment or "无",
            matched="、".join(evaluation.matched_points) or "无",
            missed="、".join(evaluation.missed_points) or "无",
            asked_follow_ups="\n".join(f"- {q}" for q in asked) or "（本轮尚未追问）",
        )

        try:
            response = await self.llm.chat_with_retry(
                messages=[Message(role=Role.USER, content=prompt)],
                temperature=0.5,
                max_tokens=1000,
            )
            data = self._parse_json(response.content)
            if not data or "continue" not in data:
                return {"continue_follow_up": None, "question": "", "reason": ""}
            return {
                "continue_follow_up": bool(data.get("continue")),
                "question": (data.get("question") or "").strip(),
                "reason": data.get("reason", ""),
            }
        except Exception:
            # Agent 不可用 → 让调用方回退（不中断面试）
            return {"continue_follow_up": None, "question": "", "reason": ""}

    def _parse_json(self, text: str) -> dict:
        """清理 markdown 包装后解析 JSON"""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
