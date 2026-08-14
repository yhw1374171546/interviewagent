"""
向量记忆真实链路测试（ChromaDB 可选）
====================================
验证 VectorMemory / InterviewMemory 在 chroma 后端下的真实语义检索。

设计:
    - chromadb 未安装 → pytest.importorskip 跳过（CI 最小依赖不受影响）
    - sentence-transformers 模型首次加载会从 HF 下载（约 80MB），
      需要网络；下载失败/离线时跳过（记忆降级逻辑有独立测试覆盖）
    - 模型实例用模块级 fixture 共享（重复加载有 HF 缓存锁偶发冲突）
    - 本地跑: 需先 pip install chromadb sentence-transformers tiktoken

运行:
    pytest tests/test_vector_memory.py -v
"""

import os
import sys
from pathlib import Path

# ⚠️ 环境变量必须在 import huggingface_hub/transformers 之前设置：
# 这些库在 import 时把 HF_ENDPOINT/HF_HUB_OFFLINE 缓存为模块常量，
# 之后修改 os.environ 无效（pytest 实测: offline 失效 + HEAD 请求打向
# huggingface.co 直连超时 + 5 次重试 = 每次测试卡 5-10 分钟）。
os.environ["HF_ENDPOINT"] = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")
# 本组测试要求模型已缓存（离线加载，秒级）；缓存缺失 → skip 而非在线下载
# （在线下载在代理不稳定环境会触发 huggingface_hub 5 次超时重试）
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

# chromadb 1.x 默认开启 OpenTelemetry 遥测（grpc exporter），在 pytest 环境
# 可能挂起主线程，必须在 import chromadb 之前禁用。
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_TELEMETRY_IMPL"] = "none"
os.environ["CHROMA_OTEL_COLLECTION_ENDPOINT"] = ""

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 可选依赖: 缺 chromadb 直接跳过整组（CI 不装）
pytest.importorskip("chromadb")
pytest.importorskip("sentence_transformers")

# 模型下载可能因网络失败 → 本组测试允许跳过（离线环境）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from interview.memory_context import InterviewMemory, MemoryEntry  # noqa: E402
from memory.vector_store import VectorMemory  # noqa: E402

# 测试用独立持久化目录，避免污染真实数据
_TEST_DIR = str(Path(__file__).resolve().parent.parent / "data" / "chroma_test")


def _load_vector_memory() -> VectorMemory | None:
    """
    离线加载 VectorMemory（模型必须已缓存，秒级完成）。

    不在测试里在线下载模型: huggingface_hub 在 import 时缓存 ENDPOINT/offline
    常量，运行期改环境变量无效，在线路径在代理不稳定环境会触发
    5 次超时重试（实测每次测试卡 5-10 分钟）。
    首次使用请先手动下载模型:
        HF_ENDPOINT=https://hf-mirror.com python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
    """
    return VectorMemory(persist_dir=_TEST_DIR)


@pytest.fixture(scope="module")
def vm() -> VectorMemory:
    """模块级共享实例（模型只加载一次，避免 HF 缓存锁并发冲突）"""
    try:
        memory = _load_vector_memory()
        memory.clear()
        return memory
    except Exception:
        pytest.skip("sentence-transformers 模型加载失败（需网络访问 HF）")


class TestVectorMemoryReal:

    def test_remember_recall_semantic_ranking(self, vm):
        """真实语义检索: 相关条距离更近（不是关键词匹配）"""
        vm.clear()
        vm.remember("候选人 Python 熟练，熟悉 FastAPI 与异步编程",
                    metadata={"category": "skill", "skill": "python"})
        vm.remember("候选人 Java 经验较少，但熟悉 Spring Boot",
                    metadata={"category": "skill", "skill": "java"})
        vm.remember("候选人在字节跳动做过推荐系统实习",
                    metadata={"category": "experience", "skill": "recommend"})

        hits = vm.recall("候选人的 Python 技能如何？", top_k=3)
        assert len(hits) == 3
        # 语义相关性: Python 条应排第一（距离最小）
        assert "Python" in hits[0]["content"]
        # 无关的 Java 条应排在最后
        assert "Java" in hits[-1]["content"]

        # 业务场景: JD 技能词（英文）检索中文记忆（跨语言语义检索）
        hits2 = vm.recall("python fastapi async", top_k=3)
        assert "Python" in hits2[0]["content"], f"英文技能词未命中 Python 记忆: {hits2[0]}"

    def test_metadata_filter(self, vm):
        vm.clear()
        vm.remember("候选人熟悉 Docker 部署", metadata={"category": "ops"})
        vm.remember("候选人熟悉 FastAPI", metadata={"category": "dev"})

        hits = vm.recall("技术能力", top_k=5, filter_meta={"category": "dev"})
        assert len(hits) == 1
        assert "FastAPI" in hits[0]["content"]

    def test_stats_and_persistence(self, vm):
        vm.clear()
        vm.remember("测试持久化一条记忆", metadata={"category": "t"})
        assert vm.stats()["total_memories"] == 1
        # 重新实例化（同一持久化目录）→ 数据仍在（跨进程持久化）
        vm2 = _load_vector_memory()
        assert vm2 is not None
        assert vm2.stats()["total_memories"] == 1
        vm2.clear()


class TestInterviewMemoryChromaBackend:

    def _chroma_backend_memory(self) -> InterviewMemory:
        """触发 chroma 初始化（复用离线容错加载逻辑）"""
        # 先用容错逻辑验证模型可加载（网络抖动时不硬失败）
        probe = _load_vector_memory()
        assert probe is not None, "模型加载失败，无法验证 chroma 后端"
        m = InterviewMemory(persist_dir=_TEST_DIR)
        assert m._ensure_chroma(), "chroma 初始化失败"
        assert m.backend == "chroma"
        m._chroma.clear()
        return m

    def test_chroma_backend_used(self):
        """装好 chromadb 时，InterviewMemory 应真实启用 chroma 后端"""
        m = self._chroma_backend_memory()
        m._chroma.clear()

    def test_recall_weaknesses_via_vector(self):
        """跨会话弱项检索: 低分技能被召回，高分不召回"""
        m = self._chroma_backend_memory()
        m._chroma.clear()
        m.remember_answer(MemoryEntry(
            question="GIL 原理", answer="全局解释器锁",
            score=6.0, category="Python", question_type="technical",
            session_id="s1", skills=["python"],
        ))
        m.remember_answer(MemoryEntry(
            question="Redis 缓存一致性", answer="Cache Aside",
            score=5.0, category="中间件", question_type="technical",
            session_id="s1", skills=["redis"],
        ))
        m.remember_answer(MemoryEntry(
            question="Docker 网络", answer="bridge",
            score=8.5, category="部署", question_type="technical",
            session_id="s1", skills=["docker"],
        ))

        weak = m.recall_weaknesses(["python", "redis", "mysql"], top_k=5)
        joined = " | ".join(weak)
        assert "Python" in joined, "Python 弱项未召回"
        assert "中间件" in joined, "Redis 弱项未召回"
        assert "部署" not in joined, "高分 Docker 不应作为弱项召回"
        m._chroma.clear()
