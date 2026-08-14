"""
LeetCode 题库导入器
===================
从 LeetCode Problems JSON Dataset（data/leetcode/leetcode_all.json，2913 题）
选取 100 道经典题，生成 interview/leetcode_bank.py（题库数据）。

选取策略:
    - 难度均衡: Easy 40 / Medium 45 / Hard 15
    - 主题覆盖: 同一主题最多选 3 道，避免集中
    - 经典优先: 按 frontend_id（题目编号）排序，靠前的是经典高频题

每道题转换:
    - id: LC + 题号（如 LC001）
    - type: coding（代码实操）
    - question: 【LeetCode 题号】英文标题 + 题目描述（截断）
    - tags/expected_points: topics（英文主题标签）
    - code: 尽力从 examples 生成测试用例（参数是 Python 字面量的题；
            树/链表等复杂题不生成，走 LLM 评估）

运行:
    python tools/import_leetcode.py
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA_FILE = ROOT / "data" / "leetcode" / "leetcode_all.json"
OUT_FILE = ROOT / "interview" / "leetcode_bank.py"
QA_OUT_FILE = ROOT / "interview" / "leetcode_solutions.py"

# 难度配额与题库难度映射
QUOTA = {"Easy": 40, "Medium": 45, "Hard": 15}
DIFF_MAP = {"Easy": 2, "Medium": 3, "Hard": 4}
MAX_PER_TOPIC = 6


def load_problems() -> list[dict]:
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)["questions"]


def select_100(problems: list[dict]) -> list[dict]:
    """难度均衡 + 主题覆盖 + 经典优先"""
    problems.sort(key=lambda p: int(p["frontend_id"]))
    selected = []
    topic_count: dict[str, int] = {}
    diff_count = {"Easy": 0, "Medium": 0, "Hard": 0}

    for p in problems:
        d = p.get("difficulty")
        if d not in QUOTA or diff_count[d] >= QUOTA[d]:
            continue
        topics = p.get("topics") or []
        key = topics[0] if topics else "other"
        if topic_count.get(key, 0) >= MAX_PER_TOPIC:
            continue
        topic_count[key] = topic_count.get(key, 0) + 1
        diff_count[d] += 1
        selected.append(p)
        if len(selected) >= 100:
            break
    return selected


# ── 测试用例生成（尽力而为） ────────────────────────────────────

def extract_signature(prob: dict) -> tuple[str, list[str]] | None:
    """从 python3 code_snippet 提取 (方法名, 参数名列表)"""
    snippets = prob.get("code_snippets") or {}
    code = snippets.get("python3", "")
    m = re.search(r"def\s+(\w+)\s*\(self\s*,\s*([^)]*)\)", code)
    if not m:
        return None
    method = m.group(1)
    params = [p.strip().split(":")[0].strip() for p in m.group(2).split(",") if p.strip()]
    return method, params


def parse_example(text: str) -> tuple[dict | None, str | None]:
    """解析 example_text → (参数 dict, 期望输出原文)"""
    inp = re.search(r"Input:\s*(.+)", text)
    out = re.search(r"Output:\s*(.+)", text)
    if not inp or not out:
        return None, None
    params = {}
    for m in re.finditer(r"(\w+)\s*=\s*(.+?)(?=,\s*\w+\s*=|$)", inp.group(1)):
        params[m.group(1)] = m.group(2).strip()
    return params, out.group(1).strip().rstrip(".")


def safe_literal(s: str):
    try:
        return ast.literal_eval(s.replace("null", "None"))
    except (ValueError, SyntaxError):
        return None


def normalize_expected(text: str) -> str:
    """期望输出规范化：list/dict/number 用 repr（print 输出一致），字符串去引号"""
    text = text.strip().replace("true", "True").replace("false", "False")
    val = safe_literal(text)
    if val is None:
        return text
    if isinstance(val, str):
        return text.strip('"\'')
    return repr(val)


def gen_test_cases(prob: dict) -> list[dict]:
    """从 examples 生成测试用例；任何一步失败都返回空（走 LLM 评估）"""
    sig = extract_signature(prob)
    if not sig:
        return []
    method, params = sig
    cases = []
    for ex in prob.get("examples", []):
        inp, out = parse_example(ex.get("example_text", ""))
        if inp is None or out is None:
            return []
        kwargs = {}
        for name, val in inp.items():
            if name not in params:
                return []
            parsed = safe_literal(val)
            if parsed is None:
                return []
            kwargs[name] = parsed
        code = f"sol = Solution()\nprint(sol.{method}({', '.join(f'{k}={v!r}' for k, v in kwargs.items())}))"
        cases.append({"name": f"用例{len(cases) + 1}", "input_code": code, "expected": normalize_expected(out)})
    return cases


# ── 生成题库文件 ─────────────────────────────────────────────────

def to_bank_entry(prob: dict, index: int) -> dict:
    """LeetCode 题 → 题库 BankQuestion dict"""
    title = prob.get("title", "")
    qid = prob.get("frontend_id", "")
    topics = prob.get("topics") or []
    desc = (prob.get("description") or "").strip()
    desc = re.sub(r"\n+", "\n", desc)[:600]

    return {
        "id": f"LC{int(qid):03d}",
        "type": "coding",
        "category": topics[0] if topics else "LeetCode",
        "question": f"【LeetCode {qid}】{title}\n\n{desc}",
        "tags": topics,
        "expected_points": topics,
        "difficulty": DIFF_MAP.get(prob.get("difficulty"), 3),
        "code": {
            "language": "python",
            "function_signature": prob.get("code_snippets", {}).get("python3", "").strip(),
            "test_cases": gen_test_cases(prob),
        },
    }


def to_qa_entry(prob: dict) -> dict:
    """LeetCode 题 → 面经条目（从官方 solution 提取英文参考答案，供 RAG 检索英文题）"""
    qid = prob.get("frontend_id", "")
    title = prob.get("title", "")
    desc = re.sub(r"\n+", " ", (prob.get("description") or ""))[:200]
    answer = extract_solution_answer(prob)
    return {
        "question": f"【LeetCode {qid}】{title} {desc}",
        "answer": answer,
        "tags": prob.get("topics") or [],
    }


def extract_solution_answer(prob: dict) -> str:
    """从官方 solution 提取精简算法思路（清理 markdown 后截断）"""
    sol = prob.get("solution") or ""
    sol = re.sub(r"\[TOC\]|^-{3,}$", "", sol, flags=re.M)
    sol = re.sub(r"\*\*([^*]+)\*\*", r"\1", sol)
    sol = re.sub(r"`([^`]+)`", r"\1", sol)
    sol = re.sub(r"^#{1,6}\s*", "", sol, flags=re.M)
    sol = re.sub(r"^\s*[-*]\s*", "", sol, flags=re.M)
    sol = re.sub(r"\n{3,}", "\n\n", sol)
    return sol.strip()[:800]


def render_file(entries: list[dict]) -> str:
    lines = [
        '"""',
        "LeetCode 精选 100 题（由 tools/import_leetcode.py 自动生成，勿手改）",
        "数据源: LeetCode Problems JSON Dataset（2913 题）",
        '"""',
        "",
        "# 每项与 BankQuestion 字段对应（type 均为 coding）",
        "LC_QUESTIONS = [",
    ]
    for e in entries:
        lines.append("    {")
        for k, v in e.items():
            lines.append(f"        {json.dumps(k)}: {json.dumps(v, ensure_ascii=False, indent=None)},")
        lines.append("    },")
    lines.append("]")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    problems = load_problems()
    selected = select_100(problems)
    entries = [to_bank_entry(p, i) for i, p in enumerate(selected)]

    # 统计
    from collections import Counter
    diff = Counter(p["difficulty"] for p in selected)
    with_test = sum(1 for e in entries if e["code"]["test_cases"])
    topics = Counter(t for e in entries for t in e["tags"])

    OUT_FILE.write_text(render_file(entries), encoding="utf-8")

    # 同时生成英文面经条目（官方 solution 提取），供 RAG 检索英文题
    qa_entries = [to_qa_entry(p) for p in selected]
    qa_lines = [
        '"""',
        "LeetCode 英文面经（由 tools/import_leetcode.py 自动生成，勿手改）",
        "答案来自官方 solution，供 RAG 检索英文题",
        '"""',
        "",
        "LC_QA_ENTRIES = [",
    ]
    for e in qa_entries:
        qa_lines.append("    " + json.dumps(e, ensure_ascii=False))
        qa_lines[-1] += ","
    qa_lines.append("]")
    qa_lines.append("")
    QA_OUT_FILE.write_text("\n".join(qa_lines), encoding="utf-8")

    print(f"已选取 {len(entries)} 道 → {OUT_FILE.name}")
    print(f"难度分布: {dict(diff)}")
    print(f"带自动判题用例: {with_test}/{len(entries)}")
    print(f"主题覆盖: {len(topics)} 个（如 {list(topics)[:8]}）")
    print(f"英文面经条目: {len(qa_entries)} → {QA_OUT_FILE.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
