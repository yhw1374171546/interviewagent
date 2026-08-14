"""
Knowledge 知识库加载器测试（阶段：知识库接入 RAG）
==================================================
覆盖 markdown 解析（单题 md + 聚合 index.md）、去重、容错。
依赖 docs/knowledge（随仓库提交），CI 可直接运行。
"""

from interview.knowledge_loader import load_knowledge_entries
from interview.qa_bank import KNOWLEDGE_DIR, get_knowledge_entries


class TestKnowledgeLoader:

    def test_loads_substantial_entries(self):
        """知识库应解析出大量面经条目（内置 22 条之外的主要数据源）"""
        entries = get_knowledge_entries()
        assert len(entries) > 100  # 实际约 397 条

    def test_entries_have_question_and_answer(self):
        entries = get_knowledge_entries()
        assert all(e.question and e.answer for e in entries)
        assert all(e.tags for e in entries)  # 目录名维度 tag

    def test_entries_deduped(self):
        entries = get_knowledge_entries()
        questions = [e.question for e in entries]
        assert len(questions) == len(set(questions))

    def test_missing_dir_returns_empty(self, tmp_path):
        entries = load_knowledge_entries(tmp_path / "nonexistent")
        assert entries == []

    def test_knowledge_dir_exists(self):
        assert KNOWLEDGE_DIR.exists()
