"""
Knowledge 知识库加载器
======================
把 `docs/knowledge/` 下的 Markdown 面试知识库（Agent 开发方向）
解析成 `QaEntry`，接入 `QaRetriever` 作为 RAG 数据源——

面经库从内置 22 条 → 数百条，覆盖架构/工程/RAG/评测/多智能体等方向。

支持两种文件格式:
  1. 单题 md（如 `01-architecture/context-window.md`）:
     `## Q：问题` + `**高手答**` + `## 考察点` + `## 追问`
  2. 聚合 index.md（长文，如 `01-architecture-design/index.md`）:
     按 `### Q：` / `## Q：` 切分，提取每段的「高手答」

设计: 尽力而为的解析 —— 单题 md 精确，index.md 按段落切分（含少量杂质但
仍是真实面经内容）。条目按问题去重，维度 tag 取目录名（去编号前缀）。
"""

from __future__ import annotations

import re
from pathlib import Path

from .qa_bank import QaEntry


def _clean(text: str) -> str:
    """清理 markdown 标记（标题/加粗/引用/列表符号）"""
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = text.replace("**", "").replace("`", "")
    text = re.sub(r"^>\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*•]\s*", "", text, flags=re.M)
    text = re.sub(r"^#{2,6}\s*Q[:：]\s*", "", text, flags=re.M)
    return text.strip()


def _dimension_from_dir(dirname: str) -> str:
    """目录名 → 维度 tag（去编号前缀: 01-architecture-design → architecture-design）"""
    return re.sub(r"^\d+-", "", dirname)


def _extract_expert_answer(segment: str) -> str:
    """从段落里提取「高手答」文本"""
    am = re.search(r"\*\*高手答\*\*\s*[:：]?\s*(.+)", segment, re.S)
    if not am:
        return ""
    answer = am.group(1)
    # 截到追问/差距/下一个标题
    answer = re.split(r"\*\*追问|\*\*差距在哪|\*\*新手答|^#{2,6}\s", answer)[0]
    return _clean(answer)


def _parse_single_question(text: str) -> tuple[str, str] | None:
    """单题 md（`## Q：` 二级标题）→ (question, answer)"""
    qm = re.search(r"^##\s*Q[:：]\s*(.+)", text, re.M)
    if not qm:
        return None
    question = qm.group(1).strip()
    answer = _extract_expert_answer(text)
    if question and answer:
        return question, answer
    return None


def _parse_index_questions(text: str) -> list[tuple[str, str]]:
    """聚合 index.md → [(question, answer), ...]（按 Q 切分）"""
    pattern = re.compile(r"(?:^|\n)#{2,4}\s*Q[:：]\s*(.+)")
    matches = list(pattern.finditer(text))
    results = []
    for i, m in enumerate(matches):
        question = m.group(1).strip()
        seg_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        seg = text[m.end():seg_end]
        answer = _extract_expert_answer(seg)
        if question and answer:
            results.append((question, answer))
    return results


def load_knowledge_entries(knowledge_dir: str | Path) -> list[QaEntry]:
    """
    扫描知识库目录，解析所有 .md 为 QaEntry 列表（按问题去重）。

    Args:
        knowledge_dir: 知识库根目录（如 docs/knowledge）
    """
    entries: list[QaEntry] = []
    seen: set[str] = set()
    base = Path(knowledge_dir)

    for path in sorted(base.rglob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        dimension = _dimension_from_dir(path.parent.name)
        parsed: list[tuple[str, str]] = []

        # 判断文件类型: `### Q：`(三级) → 聚合长文，按 Q 切分多条；
        # `## Q：`(二级) → 单题；两者都没有 → 非题库文档（方法论），跳过
        if re.search(r"^###\s*Q[:：]", text, re.M):
            parsed.extend(_parse_index_questions(text))
        elif re.search(r"^##\s*Q[:：]", text, re.M):
            single = _parse_single_question(text)
            if single:
                parsed.append(single)

        for q, a in parsed:
            if q in seen:
                continue
            seen.add(q)
            entries.append(QaEntry(
                id=f"kb:{len(entries) + 1}",
                question=q,
                answer=a[:2000],  # 答案截断，避免超长
                tags=[dimension],
            ))

    return entries
