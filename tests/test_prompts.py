"""
Prompt 集中管理测试（D1）
========================
覆盖 interview/prompts.py:
    1. 注册表完整性（每个 prompt 有生效版本，版本存在）
    2. A/B 切换（set_prompt_version 运行时生效 / 非法版本拒绝 / 切换后渲染正常）
    3. 全部 prompt 用典型占位符渲染不抛错（防 format 漂移）
    4. Agent 兼容常量存在
全部离线纯函数，CI 可直接运行。
"""

import pytest

from interview.prompts import (
    CODER_SYSTEM_PROMPT,
    PROMPT_REGISTRY,
    RESEARCH_SYSTEM_PROMPT,
    active_prompt,
    prompt_version,
    prompt_versions,
    render_prompt,
    set_prompt_version,
)


class TestRegistry:

    def test_all_prompts_have_active_version(self):
        """每个注册 prompt 都有生效版本且版本已注册"""
        for name, entry in PROMPT_REGISTRY.items():
            assert entry["active"], f"{name} 未设置生效版本"
            assert entry["active"] in entry["versions"], f"{name} 生效版本未注册"
            assert entry["versions"][entry["active"]].strip(), f"{name} 版本文本为空"

    def test_registry_has_expected_prompts(self):
        """面试域 + Agent 域 prompt 全集"""
        expected = {
            "warmup", "jd_fallback", "deep_eval", "code_review",
            "follow_up_agent", "final_report", "stream_report", "arbiter",
            "question_customize", "question_generate",
            "agent_coder", "agent_research",
        }
        assert set(PROMPT_REGISTRY) == expected

    def test_active_prompt_returns_current(self):
        """active_prompt 返回当前生效版本文本"""
        assert active_prompt("warmup") == PROMPT_REGISTRY["warmup"]["versions"]["v1"]
        assert "开场白" in active_prompt("warmup")

    def test_agent_compat_constants(self):
        """Agent 模块兼容常量 = 当前生效版本"""
        assert CODER_SYSTEM_PROMPT == active_prompt("agent_coder")
        assert RESEARCH_SYSTEM_PROMPT == active_prompt("agent_research")


class TestABSwitching:

    def test_switch_changes_active(self):
        """A/B 切换: 注册 v2 → 切换 → active_prompt 返回 v2"""
        PROMPT_REGISTRY["warmup"]["versions"]["v2"] = "v2 开场白（测试）"
        try:
            assert prompt_version("warmup") == "v1"
            set_prompt_version("warmup", "v2")
            assert prompt_version("warmup") == "v2"
            assert active_prompt("warmup") == "v2 开场白（测试）"
            assert "v2" in prompt_versions("warmup")
        finally:
            set_prompt_version("warmup", "v1")
            del PROMPT_REGISTRY["warmup"]["versions"]["v2"]
        assert prompt_version("warmup") == "v1"

    def test_switch_unknown_version_rejected(self):
        """非法版本 → KeyError，且当前版本不变"""
        with pytest.raises(KeyError):
            set_prompt_version("warmup", "v99")
        assert prompt_version("warmup") == "v1"

    def test_switch_unknown_name_rejected(self):
        """未知 prompt 名 → KeyError"""
        with pytest.raises(KeyError):
            set_prompt_version("not_a_prompt", "v1")

    def test_render_after_switch(self):
        """切换后渲染仍工作（占位符随版本走）"""
        PROMPT_REGISTRY["warmup"]["versions"]["v2"] = "v2 {position} {skills}"
        try:
            set_prompt_version("warmup", "v2")
            text = render_prompt("warmup", position="后端", skills="Python")
            assert text == "v2 后端 Python"
        finally:
            set_prompt_version("warmup", "v1")
            del PROMPT_REGISTRY["warmup"]["versions"]["v2"]


class TestRender:

    def test_all_prompts_render_with_typical_args(self):
        """每个 prompt 用典型占位符渲染不抛错（防 format 漂移/占位符不一致）"""
        samples = {
            "warmup": dict(position="Python 后端工程师", skills="Python, FastAPI"),
            "jd_fallback": dict(unmatched_text="熟悉分布式系统设计"),
            "deep_eval": dict(
                question="解释 GIL", expected_points="GIL, 多线程",
                answer="GIL 是全局解释器锁…",
            ),
            "code_review": dict(question="实现 LRU", answer="def lru(): pass"),
            "follow_up_agent": dict(
                question="解释 GIL", answer="答", match_rate=0.6, comment="一般",
                matched="GIL", missed="多线程", asked_follow_ups="无",
            ),
            "final_report": dict(
                position="后端", skills="Python", interview_log="第1题…",
            ),
            "stream_report": dict(
                position="后端", skills="Python", interview_log="第1题…",
            ),
            "arbiter": dict(
                question="解释 GIL", expected_points="GIL", answer="答",
                judge_a="严格", judge_b="宽容",
            ),
            "question_customize": dict(
                position="后端", skills="Python", responsibilities="开发",
                q_list="[T1] Python 基础题",
            ),
            "question_generate": dict(
                needed=3, position="后端", skills="Python", focus="并发",
            ),
            "agent_coder": {},
            "agent_research": {},
        }
        for name, kwargs in samples.items():
            text = render_prompt(name, **kwargs)
            assert text.strip(), f"{name} 渲染结果为空"

    def test_missing_placeholder_raises(self):
        """缺占位符 → KeyError（尽早暴露 prompt 与调用方不一致）"""
        with pytest.raises(KeyError):
            render_prompt("warmup")  # 缺 position/skills
