"""
JD 解析器测试（阶段 3）
======================
覆盖规则提取、LLM 兜底阈值（>50 字符）、岗位猜测、malformed JSON 降级、词边界。
全部离线（FakeLLM），CI 可直接运行。
"""

import asyncio
import json

from core.llm import LLMClient, LLMResponse
from interview.jd_parser import JDAnalysis, JDParser


def run(coro):
    return asyncio.run(coro)


class _RecordingLLM(LLMClient):
    """返回固定 JSON 并记录调用次数"""

    def __init__(self, content: str):
        super().__init__(model="fake-jd")
        self.content = content
        self.calls = 0

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096, stream=False):
        self.calls += 1
        return LLMResponse(content=self.content)


# 一段不含任何 SKILL_TAXONOMY 关键词的英文描述，用于制造 >50 字符的未匹配文本
_UNMATCHED = (
    "We are hiring for a senior role focused on building reliable and scalable "
    "systems with a strong emphasis on quality and performance. "
) * 3


class TestRuleExtraction:

    def test_extracts_skills_without_llm(self):
        parser = JDParser(None)
        analysis = run(parser.parse("Python 后端工程师，要求熟悉 MySQL、Redis，3年以上经验，本科以上学历"))
        assert "Python" in analysis.all_skills
        assert "MySQL" in analysis.all_skills
        assert analysis.experience == "3年以上"
        assert analysis.education != ""

    def test_preferred_skills_separated(self):
        parser = JDParser(None)
        # 加分判断按「行」为界: 必须技能与加分技能须分行，否则整行 context 会被"优先/了解"污染
        analysis = run(parser.parse("精通 Python\n了解 Docker 者优先"))
        assert "Python" in analysis.required_skills
        assert "Docker" in analysis.preferred_skills

    def test_word_boundary_no_substring_mismatch(self):
        """短关键词词边界: 'go' 不能误命中 'django'，'java' 不能误命中 'javascript'"""
        parser = JDParser(None)
        analysis = run(parser.parse("熟悉 Django 与 JavaScript"))
        skill_names = {s.lower() for s in analysis.all_skills}
        assert "go" not in skill_names
        assert "java" not in skill_names
        assert "Django" in analysis.all_skills

    def test_agent_short_jd_extracts_ai_agent_skill(self):
        """短 JD（仅岗位名「agent开发工程师」）也能提取 AI Agent 技能 —
        修复「JD 无技能 → 通用补齐 → 不出 agent 题」的问题"""
        parser = JDParser(None)
        analysis = run(parser.parse("agent开发工程师"))
        assert "AI Agent" in analysis.all_skills
        assert analysis.position == "agent开发工程师"


class TestLLMFallback:

    def test_fallback_triggers_over_50_chars(self):
        llm = _RecordingLLM(json.dumps({
            "position": "后端工程师",
            "domain_knowledge": ["电商"],
            "responsibilities": ["负责系统设计"],
            "interview_focus": ["系统设计"],
            "missing_skills": ["FastAPI"],
        }))
        parser = JDParser(llm)
        jd = "Python 后端工程师，要求 MySQL。\n" + _UNMATCHED
        analysis = run(parser.parse(jd))
        assert llm.calls == 1
        assert analysis.position == "后端工程师"
        assert "FastAPI" in analysis.all_skills  # LLM 补充的 missing_skills 合并
        assert analysis.interview_focus == ["系统设计"]

    def test_fallback_skipped_under_50_chars(self):
        llm = _RecordingLLM("{}")
        parser = JDParser(llm)
        analysis = run(parser.parse("Python 后端工程师，要求 FastAPI MySQL Redis。"))
        assert llm.calls == 0
        assert "Python" in analysis.all_skills

    def test_malformed_json_degrades_to_rules(self):
        llm = _RecordingLLM("not json {{{")
        parser = JDParser(llm)
        jd = "Python 后端工程师，要求 MySQL。\n" + _UNMATCHED
        analysis = run(parser.parse(jd))
        # 不崩溃，position 走规则猜测兜底
        assert analysis.position != ""
        assert "Python" in analysis.all_skills


class TestGuessPosition:

    def test_from_explicit_intent(self):
        parser = JDParser(None)
        assert parser._guess_position("求职意向：Java 开发工程师\n其他") == "Java 开发工程师"

    def test_keyword_fallback(self):
        parser = JDParser(None)
        pos = parser._guess_position("高级后端开发工程师，负责核心系统")
        assert "工程师" in pos

    def test_unrecognized(self):
        parser = JDParser(None)
        assert parser._guess_position("这里没有任何岗位相关词") == "未识别"


class TestDefaultFocus:

    def test_default_focus_uses_first_skill(self):
        parser = JDParser(None)
        analysis = JDAnalysis(required_skills=["Python", "MySQL", "Redis"])
        focus = parser._default_focus(analysis)
        assert len(focus) <= 3
        assert any("Python" in f for f in focus)


class TestJDAnalysis:

    def test_all_skills_dedup_case_insensitive(self):
        a = JDAnalysis(required_skills=["Python", "python"], preferred_skills=["MySQL"])
        assert a.all_skills == ["Python", "MySQL"]

    def test_summary_contains_position(self):
        a = JDAnalysis(position="后端工程师", required_skills=["Python"])
        assert "后端工程师" in a.summary()


class TestCoverageReport:

    def test_coverage_report_fields(self):
        parser = JDParser(None)
        report = parser.coverage_report("Python 后端工程师，要求 MySQL")
        assert "total_chars" in report
        assert report["skills_found"] >= 1
