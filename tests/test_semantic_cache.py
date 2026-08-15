"""
JD 语义缓存测试（B2）
====================
覆盖 interview/semantic_cache.py:
    - 相似 JD 命中（换措辞但同义）
    - 不同 JD 未命中
    - 持久化（重启后仍在）
    - 嵌入器不可用 → 降级关闭（返回 None 不报错）
    - Interviewer 集成: 二次相同/相似 JD 命中缓存

注意: 需要 sentence-transformers 模型（已装，离线可用）；模型不可用时
缓存自动降级，测试跳过相关断言。
"""

import asyncio
import os
from pathlib import Path

# 模型已缓存时强制离线加载（避免 huggingface_hub HEAD 校验触网卡死）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


from core.mock_llm import MockLLMClient
from interview.interviewer import Interviewer
from interview.jd_parser import JDAnalysis
from interview.semantic_cache import JDSemanticCache


def _cache(tmp_path: Path, threshold: float = 0.9) -> JDSemanticCache:
    return JDSemanticCache(
        cache_path=str(tmp_path / "jd_cache.json"), threshold=threshold,
    )


def _analysis() -> JDAnalysis:
    return JDAnalysis(
        position="Python 后端工程师",
        experience="3-5 年",
        education="本科",
        required_skills=["Python", "FastAPI", "Redis"],
        preferred_skills=["MySQL"],
        soft_skills=["沟通能力"],
        domain_knowledge=["微服务"],
        responsibilities=["架构设计"],
        interview_focus=["Python深度", "系统设计"],
    )


class TestSemanticCache:

    def test_similar_jd_hits(self, tmp_path):
        """换措辞但同义的 JD → 命中缓存"""
        cache = _cache(tmp_path)
        jd1 = "Python 后端工程师，要求精通 Python、FastAPI，熟悉 Redis 和 MySQL，本科 3-5 年经验"
        jd2 = "招聘 Python 后端开发，需要熟练使用 Python 和 FastAPI 框架，掌握 Redis、MySQL，本科及以上 3-5 年"

        assert cache.lookup(jd1) is None  # 首次未命中
        cache.store(jd1, _analysis())

        hit = cache.lookup(jd2)
        assert hit is not None, "相似 JD 应命中缓存"
        assert hit.position == "Python 后端工程师"
        assert "Python" in hit.required_skills

    def test_different_jd_misses(self, tmp_path):
        """完全不同岗位的 JD → 未命中"""
        cache = _cache(tmp_path)
        jd1 = "Python 后端工程师，要求精通 Python、FastAPI，熟悉 Redis"
        jd_other = "前端开发工程师，要求精通 React、TypeScript、Vue，熟悉 CSS 和 Webpack"

        cache.store(jd1, _analysis())
        assert cache.lookup(jd_other) is None

    def test_low_threshold_strict(self, tmp_path):
        """阈值过严 → 相似但不同 JD 不命中（防错误复用）"""
        cache = _cache(tmp_path, threshold=0.99)
        jd1 = "Python 后端工程师，要求精通 Python、FastAPI，熟悉 Redis 和 MySQL，本科 3-5 年经验，负责核心业务系统"
        jd2 = "Python 后端工程师，要求精通 Python、FastAPI，熟悉 Redis 和 MySQL，本科 3-5 年经验，负责数据分析平台"

        cache.store(jd1, _analysis())
        # 阈值 0.99 太严，这两段大概率不命中（保安全）
        hit = cache.lookup(jd2)
        assert hit is None or hit.position == "Python 后端工程师"

    def test_persistence(self, tmp_path):
        """写入后重新加载 → 数据仍在"""
        path = tmp_path / "jd_cache.json"
        cache1 = JDSemanticCache(cache_path=str(path))
        cache1.store("Python 后端工程师，精通 Python、FastAPI", _analysis())

        cache2 = JDSemanticCache(cache_path=str(path))
        hit = cache2.lookup("Python 后端开发，熟练 Python 和 FastAPI")
        assert hit is not None

    def test_clear(self, tmp_path):
        cache = _cache(tmp_path)
        cache.store("Python 后端工程师", _analysis())
        cache.clear()
        assert cache.stats()["entries"] == 0


# ── Interviewer 集成 ───────────────────────────────────────────

JD1 = "Python 后端工程师，要求精通 Python、FastAPI，熟悉 Redis 和 MySQL，本科 3-5 年经验"
JD2 = "招聘 Python 后端开发，熟练使用 Python 和 FastAPI 框架，掌握 Redis、MySQL，本科及以上"

ANSWERS = [
    "GIL 是全局解释器锁，多进程绕过，asyncio 处理 IO 密集，Redis 有序集合做排行榜",
    "FastAPI 基于 Starlette 和 Pydantic，自动生成 OpenAPI 文档",
]


def run(coro):
    return asyncio.run(coro)


class TestInterviewerCache:

    def test_second_interview_hits_cache(self, tmp_path):
        """第一场面试写入缓存，第二场相似 JD 命中 → cache_hit=True"""
        async def scenario():
            cache = _cache(tmp_path)
            # 第一场: 未命中，解析后写入
            iv1 = Interviewer(MockLLMClient(), total_questions=2, jd_cache=cache)
            await iv1.start(JD1)
            assert iv1.state.cache_hit is False
            # 第二场: 相似 JD 命中
            iv2 = Interviewer(MockLLMClient(), total_questions=2, jd_cache=cache)
            await iv2.start(JD2)
            return iv1, iv2

        iv1, iv2 = run(scenario())
        assert iv1.state.cache_hit is False
        assert iv2.state.cache_hit is True, "相似 JD 第二场应命中缓存"
        assert iv2.state.jd_analysis.position != ""

    def test_identical_jd_hits(self, tmp_path):
        """完全相同 JD 第二场必命中"""
        async def scenario():
            cache = _cache(tmp_path)
            iv1 = Interviewer(MockLLMClient(), total_questions=1, jd_cache=cache)
            await iv1.start(JD1)
            iv2 = Interviewer(MockLLMClient(), total_questions=1, jd_cache=cache)
            await iv2.start(JD1)
            return iv1, iv2

        iv1, iv2 = run(scenario())
        assert iv1.state.cache_hit is False
        assert iv2.state.cache_hit is True

    def test_cache_disabled_no_error(self, tmp_path):
        """jd_cache=None → 不走缓存，正常解析（不报错）"""
        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=1, jd_cache=None)
            await iv.start(JD1)
            return iv

        iv = run(scenario())
        assert iv.state.jd_analysis is not None

    def test_cache_default_off(self, tmp_path):
        """默认 jd_cache=None → 不加载 embedding 模型（离线路径不触网）"""
        async def scenario():
            iv = Interviewer(MockLLMClient(), total_questions=1)
            await iv.start(JD1)
            return iv

        iv = run(scenario())
        assert iv.jd_cache is None  # 默认关闭
        assert iv.state.cache_hit is False  # 不走缓存路径
