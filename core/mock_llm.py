"""
Mock LLM 客户端
===============
零 API 调用、确定性的模拟 LLM，用于无 Key 演示和离线测试。

实现方式: 复用项目的规则引擎思想 — 不"生成"答案，而是按 prompt 类型
返回确定性的响应。Mock 不追求智能，只保证:

    1. 确定性 — 同样的输入永远得到同样的输出（可测试、可复现）
    2. 结构合规 — 严格按各 prompt 要求的 JSON Schema 返回
    3. 覆盖全流程 — 暖场/JD解析/题目微调/评估/报告 每个环节都能走通

为什么有这个类:
    - Web Demo 在没有 API Key 的环境下也能完整演示面试流程
    - 单元测试不依赖外部 API（确定性 → 可断言）
    - 面试可讲: LLMClient 是抽象基类，Mock 是它的一个实现 —
      这正是"面向接口编程"的体现，换真实模型只需改配置

使用:
    from core.mock_llm import MockLLMClient
    llm = MockLLMClient()
    response = await llm.chat(messages=[...])
"""

from __future__ import annotations

import json
import re

from .llm import LLMClient, LLMResponse, Message, Role


class MockLLMClient(LLMClient):
    """确定性 Mock LLM — 按 prompt 内容路由到对应的规则响应"""

    def __init__(self, model: str = "mock-1"):
        super().__init__(model, api_key=None, base_url=None)

    async def chat(
        self,
        messages: list[Message],
        tools=None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = False,
    ) -> LLMResponse:
        # 取最后一条 user 消息作为判断依据
        prompt = ""
        for m in reversed(messages):
            if m.role == Role.USER:
                prompt = m.content
                break

        if "开场白" in prompt:
            content = self._warmup()
        elif "follow_up_decision" in prompt:
            content = self._evaluation(prompt)
        elif "overall_score" in prompt and "面试记录" in prompt:
            content = self._report(prompt)
        elif "微调" in prompt and "当前题目" in prompt:
            content = "[]"  # 题库微调: 不修改题目
        elif "生成" in prompt and "面试题" in prompt:
            content = "[]"  # LLM 补充出题: 题库已足够
        elif "未匹配的 JD 文本" in prompt or "missing_skills" in prompt:
            content = self._jd_fallback(prompt)
        else:
            content = "好的，我们继续。"

        return LLMResponse(
            content=content,
            finish_reason="stop",
            usage={"prompt_tokens": len(prompt) // 4, "completion_tokens": len(content) // 4},
        )

    # ── 各场景的确定性响应 ─────────────────────────────────

    def _warmup(self) -> str:
        return (
            "你好！欢迎参加今天的模拟面试。我是本次面试的面试官，"
            "接下来我会围绕你的简历内容提几个问题。请放松，像真实面试一样回答即可。"
        )

    def _jd_fallback(self, prompt: str) -> str:
        """JD 解析兜底 — 返回空结构，让服务器端的规则结果生效"""
        return json.dumps({
            "position": "",
            "domain_knowledge": [],
            "responsibilities": ["负责核心业务系统的开发与维护"],
            "interview_focus": ["核心技术栈", "项目经验", "问题解决能力"],
            "missing_skills": [],
        }, ensure_ascii=False)

    def _evaluation(self, prompt: str) -> str:
        """
        评估 — 确定性规则引擎（模拟真实评估器的双引擎思路）。

        修复过的两个问题:
            1. [bug] 旧正则 `(?=\n*$)` 会把「## 分析要求」等指令文本也
               截进 answer — 长度永远 >200，深度/结构恒为 8/7
            2. [设计] 只看回答长度 → 与题目无关的长回答也能拿高深度分。
               改为: 关键词命中率（对照期望要点）+ 长度 + 信息密度 三重规则
        """
        # 1. 提取回答 — 只截取到下一个 ## 标题之前
        m = re.search(r"## 面试者回答\s*\n(.+?)(?=\n+## |\Z)", prompt, re.S)
        answer = m.group(1).strip() if m else ""

        # 2. 提取期望回答要点（评估器打分用的知识点）
        m2 = re.search(r"## 期望回答要点\s*\n(.+?)(?=\n+## |\Z)", prompt, re.S)
        points_text = m2.group(1).strip() if m2 else ""

        # 3. 关键词命中率 — 与 interview/evaluator.py 的确定性引擎同思路
        answer_lower = answer.lower()
        points = [p.strip() for p in re.split(r"[,，、\n]+", points_text) if p.strip()]
        matched = 0
        for p in points:
            keywords = re.findall(r"[a-zA-Z0-9]+|[一-鿿]{2,}", p.lower())
            if keywords and sum(1 for k in keywords if k in answer_lower) >= max(1, len(keywords) * 0.5):
                matched += 1
        match_rate = matched / len(points) if points else 0.0

        # 4. 信息密度 — 检测 "GOGOGO" 这类重复字符的无效输入
        n = len(answer)
        unique_ratio = len(set(answer_lower)) / max(1, n)
        is_spam = n >= 20 and unique_ratio < 0.15

        # 5. 评分规则（命中率为主，长度和密度辅助）
        if is_spam:
            depth, structure = "表面", "松散"
            decision, fq = "deepen", "请认真回答，围绕题目考查的技术点展开。"
            comment = "回答内容无效，未触及题目任何要点。"
            strengths, weaknesses = [], ["回答无效"]
        elif n < 20:
            depth, structure = "表面", "松散"
            decision, fq = "deepen", "能展开说说吗？具体是怎么实现的？"
            comment = "回答过于简短，缺乏细节。"
            strengths, weaknesses = [], ["回答过短"]
        elif match_rate < 0.3:
            # 长但不相关 — 不能因为写得多就给高深度分
            depth, structure = "较浅", "一般"
            decision, fq = "challenge", "你的回答似乎没有触及这道题的核心概念，请围绕题目重新组织回答。"
            comment = f"回答与题目要点匹配度较低（命中 {match_rate:.0%}），内容偏离了考察方向。"
            strengths = ["表达有条理"] if n >= 80 else []
            weaknesses = ["核心要点命中不足", "回答偏离题目"]
        elif match_rate < 0.6:
            depth, structure = "适中", "清晰"
            decision, fq = "example", "能举个你实际项目中的例子吗？"
            comment = f"回答覆盖了部分要点（命中 {match_rate:.0%}），可以进一步展开细节。"
            strengths, weaknesses = ["表达有条理"], ["部分要点可以更深入"]
        else:
            depth, structure = "深入", "清晰"
            decision, fq = "move_on", ""
            comment = f"回答结构清晰，要点覆盖较全（命中 {match_rate:.0%}）。"
            strengths, weaknesses = ["表达有条理", "要点覆盖较全"], ["个别细节可以补充"]

        return json.dumps({
            "depth_level": depth,
            "structure_level": structure,
            "overall_comment": comment,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "follow_up_decision": decision,
            "follow_up_question": fq,
            "follow_up_reason": f"关键词命中 {match_rate:.0%}，长度 {n} 字",
        }, ensure_ascii=False)

    def _report(self, prompt: str) -> str:
        """报告 — 从面试记录中提取每题的评分算平均分（确定性）"""
        scores = re.findall(r"评分:\s*([\d.]+)/10", prompt)
        if scores:
            avg = round(sum(float(s) for s in scores) / len(scores), 1)
        else:
            avg = 7.0

        level = ("卓越" if avg >= 9 else "优秀" if avg >= 7.5
                 else "良好" if avg >= 6 else "一般" if avg >= 4 else "需提升")
        verdict = "推荐通过" if avg >= 7.5 else ("建议待定" if avg >= 5 else "不推荐通过")

        return json.dumps({
            "overall_score": avg,
            "overall_level": level,
            "main_strengths": ["基础扎实", "表达清晰", "学习意愿强"],
            "main_weaknesses": ["深度可以继续提升", "部分场景考虑不够全面"],
            "improvement_advice": "建议针对薄弱知识点做专题复习，多积累实际项目中的问题解决经验。",
            "verdict": verdict,
            "verdict_reason": f"综合得分 {avg}/10，{level}。",
        }, ensure_ascii=False)
