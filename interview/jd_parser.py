"""
JD 解析器 (v2 — 混合模式)
==========================
规则引擎（skill_taxonomy）优先匹配 → LLM 只处理规则覆盖不到的模糊部分。

解析流程:
    JD 文本
       │
       ├──→ 规则匹配 (200+ 关键词, 学历/经验正则) → 覆盖 ~70-90%
       │    返回: skills, education, experience, soft_skills
       │
       ├──→ LLM 兜底 (只传未匹配的文本片段) → 覆盖剩余 ~10-30%
       │    返回: responsibilities, interview_focus, 规则漏掉的技能
       │
       └──→ 合并结果 → JDAnalysis

为什么不用 LLM 全量解析:
    - 规则匹配是确定性的，同样的 JD 每次结果一样
    - 关键词匹配覆盖了 JD 中 70-90% 的技术信息
    - LLM 只处理真正需要语义理解的部分（职责描述、考察重点推断）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from core.llm import LLMClient, Message, Role

from .skill_taxonomy import get_skill_coverage_report, rule_based_extract


@dataclass
class JDAnalysis:
    """解析后的 JD 结构化信息"""
    position: str = ""
    experience: str = ""
    education: str = ""
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    soft_skills: list[str] = field(default_factory=list)
    domain_knowledge: list[str] = field(default_factory=list)
    responsibilities: list[str] = field(default_factory=list)
    interview_focus: list[str] = field(default_factory=list)

    @property
    def all_skills(self) -> list[str]:
        """所有技能（去重）"""
        seen = set()
        result = []
        for skill in self.required_skills + self.preferred_skills:
            if skill.lower() not in seen:
                seen.add(skill.lower())
                result.append(skill)
        return result

    def summary(self) -> str:
        """人类可读的摘要"""
        lines = [
            f"📋 岗位: {self.position}",
            f"📌 经验: {self.experience}",
            f"🎓 学历: {self.education}",
            f"🛠️ 技术栈: {', '.join(self.all_skills[:10])}",
        ]
        if self.soft_skills:
            lines.append(f"💬 软技能: {', '.join(self.soft_skills[:5])}")
        if self.domain_knowledge:
            lines.append(f"🏭 领域: {', '.join(self.domain_knowledge[:5])}")
        if self.interview_focus:
            lines.append(f"🎯 考察重点: {', '.join(self.interview_focus)}")
        return "\n".join(lines)


# LLM 兜底 prompt — 只处理规则搞不定的部分
LLM_FALLBACK_PROMPT = """你是一位招聘 JD 分析师。以下是一份 JD 中「规则引擎未能自动识别的内容」，请从中提取以下信息，以 JSON 返回:

1. **position**: 岗位名称（如 "高级后端开发工程师"）
2. **domain_knowledge**: 业务领域知识要求（如 "电商"、"金融风控"、"SaaS"）
3. **responsibilities**: 核心工作职责（3-5 条）
4. **interview_focus**: 根据 JD 推断，面试中最应该考察的 3 个方向
5. **missing_skills**: 规则引擎可能漏掉的技术关键词（请用标准名称，如 "PostgreSQL" 而非 "postgres"）

## 未匹配的 JD 文本
{unmatched_text}

## 输出格式
```json
{{
  "position": "",
  "domain_knowledge": [],
  "responsibilities": [],
  "interview_focus": [],
  "missing_skills": []
}}
```"""


class JDParser:
    """
    JD 解析器 — 混合模式。

    规则优先 (确定性) + LLM 兜底 (语义理解)。

    使用:
        parser = JDParser(llm_client)
        analysis = await parser.parse(jd_text)
        print(parser.coverage_report(jd_text))  # 查看规则覆盖率
    """

    def __init__(self, llm_client: LLMClient | None = None):
        """
        Args:
            llm_client: LLM 客户端。如果为 None，则只使用规则匹配。
        """
        self.llm = llm_client

    async def parse(self, jd_text: str) -> JDAnalysis:
        """
        解析 JD 文本。

        Args:
            jd_text: 原始 JD 文本

        Returns:
            JDAnalysis: 结构化的 JD 分析
        """
        # ── 阶段 1: 规则匹配 (确定性, 0 API 调用) ──
        rule_result = rule_based_extract(jd_text)

        # 分离必须技能和加分技能
        required = []
        preferred = []
        for s in rule_result.skills:
            if s["source"] == "preferred":
                preferred.append(s["name"])
            else:
                required.append(s["name"])

        analysis = JDAnalysis(
            position="",  # 规则引擎不判断岗位名
            experience=rule_result.experience,
            education=rule_result.education,
            required_skills=required,
            preferred_skills=preferred,
            soft_skills=rule_result.soft_skills,
        )

        # ── 阶段 2: LLM 兜底 (仅当有未匹配文本时) ──
        if self.llm and rule_result.unmatched_text and len(rule_result.unmatched_text) > 50:
            try:
                llm_data = await self._llm_fallback(rule_result.unmatched_text)

                analysis.position = llm_data.get("position", "")
                analysis.domain_knowledge = llm_data.get("domain_knowledge", [])

                # 合并 LLM 提取的技能（去重）
                existing_names = {s.lower() for s in analysis.all_skills}
                for skill_name in llm_data.get("missing_skills", []):
                    if skill_name.lower() not in existing_names:
                        analysis.required_skills.append(skill_name)
                        existing_names.add(skill_name.lower())

                # 职责和考察重点几乎只能靠 LLM
                analysis.responsibilities = llm_data.get("responsibilities", [])
                analysis.interview_focus = llm_data.get("interview_focus", [])

            except Exception:
                # LLM 调用失败 → 使用规则结果，不报错
                pass

        # ── 降级处理 ──
        if not analysis.position:
            analysis.position = self._guess_position(jd_text)
        if not analysis.interview_focus:
            analysis.interview_focus = self._default_focus(analysis)

        return analysis

    async def _llm_fallback(self, unmatched_text: str) -> dict:
        """LLM 兜底解析"""
        prompt = LLM_FALLBACK_PROMPT.format(unmatched_text=unmatched_text[:3000])

        response = await self.llm.chat_with_retry(
            messages=[Message(role=Role.USER, content=prompt)],
            temperature=0.2,
            max_tokens=1000,
        )

        content = response.content.strip()
        # 清理 markdown 包装
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {}

    def coverage_report(self, jd_text: str) -> dict:
        """规则引擎覆盖率报告（调试用）"""
        return get_skill_coverage_report(jd_text)

    def _guess_position(self, jd_text: str) -> str:
        """勉强猜个岗位名"""
        # 优先: 简历中的「求职意向/意向岗位」等显式声明
        import re
        m = re.search(
            r"(求职意向|意向岗位|期望职位|应聘岗位|目标岗位|期望岗位)[：:\s]*([^\n，,。]{2,40})",
            jd_text,
        )
        if m:
            return m.group(2).strip()

        # 回退: 找岗位关键词，往前截取上下文
        for keyword in ["工程师", "开发", "架构师", "经理", "设计师", "分析师", "产品经理"]:
            if keyword in jd_text:
                idx = jd_text.find(keyword)
                start = max(0, idx - 20)
                return jd_text[start:idx + len(keyword)].strip().split("\n")[-1]
        return "未识别"

    def _default_focus(self, analysis: JDAnalysis) -> list[str]:
        """默认考察重点"""
        focus = []
        if analysis.required_skills:
            focus.append(f"核心技术: {analysis.required_skills[0]}")
        if len(analysis.required_skills) > 2:
            focus.append("系统设计与架构能力")
        focus.append("项目经验与问题解决能力")
        return focus[:3]
