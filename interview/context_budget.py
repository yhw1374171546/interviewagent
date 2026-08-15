"""
评估上下文预算守卫
==================
面试链路把「历史摘要 + 跨会话弱项 + 当前题 + 回答」拼进评估 prompt，
轮数多了会无限制增长。本模块用 token 预算约束它——兑现简历里
「Context 管理: 滑动窗口 + 优先级保留」的承诺（此前 ContextManager/
ContextOptimizer 只在 memory/ 里实现，面试主链路从未接入）。

策略（确定性、可测试）:
    1. 估算评估 prompt 总 token（题目 + 回答 + 历史 + 弱项）
    2. 超预算 → 按优先级裁剪，保留顺序:
       当前题 / 回答 (CRITICAL) → 跨会话弱项 (HIGH) → 历史摘要 (MEDIUM, 可裁剪)
    3. 历史摘要裁剪: 保留最近 N 轮（越近越重要，面试官"翻旧账"主要翻最近几轮）

用法:
    from interview.context_budget import fit_eval_context
    history, hints = fit_eval_context(history_text, hints, question_len, answer_len)
"""

from __future__ import annotations

import re

# 评估 prompt 的 token 预算（字符数近似; 中文约 1 字 ≈ 1-2 token，保守取 1.5）
# 默认预算留给「题目 + 回答 + 输出空间」后，历史/弱项可用的字符额度
DEFAULT_BUDGET_CHARS = 2400          # 历史+弱项合计预算（字符）
MAX_HISTORY_ITEMS = 5                # 历史摘要最多保留几轮
MAX_HINTS_ITEMS = 3                  # 弱项最多保留几条


def _estimate_tokens(text: str) -> int:
    """字符级 token 估算（中文按 1.5 字符/token，英文按 4 字符/token）"""
    if not text:
        return 0
    cjk = len(re.findall(r"[一-鿿]", text))
    other = len(text) - cjk
    return int(cjk * 1.5 + other / 4)


def fit_eval_context(
    history_text: str,
    memory_hints: list[str] | None,
    question_len: int = 0,
    answer_len: int = 0,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
    max_history_items: int = MAX_HISTORY_ITEMS,
    max_hints_items: int = MAX_HINTS_ITEMS,
) -> tuple[str, list[str]]:
    """
    把历史摘要和弱项裁剪到预算内。

    Args:
        history_text: build_history_summary 的输出（分号分隔的轮次摘要）
        memory_hints: 跨会话弱项列表
        question_len / answer_len: 当前题和回答的字符数（用于估算总预算占用）
        budget_chars: 历史+弱项允许的最大字符数
        max_history_items / max_hints_items: 各自最多保留条数

    Returns:
        (裁剪后的历史文本, 裁剪后的弱项列表)
    """
    hints = list(memory_hints or [])[:max_hints_items]

    # 预算已被题目+回答占掉一部分 → 历史/弱项可用额度按比例收缩
    # （题目+回答是 CRITICAL，历史/弱项是 MEDIUM/HIGH，先保前者）
    squeezed = question_len + answer_len > budget_chars * 2
    available = budget_chars
    if squeezed:
        # 题目+回答已很大 → 历史/弱项只给少量空间
        available = max(200, budget_chars // 2)

    # 1. 弱项优先保留（HIGH 优先级，跨会话信息最有价值）
    hints_text = "；".join(hints)
    if _estimate_tokens(hints_text) > available // 2 or squeezed:
        hints = hints[:1]  # 只留 1 条最重要弱项
        hints_text = "；".join(hints)

    # 2. 历史摘要按轮次裁剪（保留最近 N 轮，MEDIUM 优先级可裁剪）
    items = [s.strip() for s in history_text.split(";") if s.strip()]
    if not items:
        return "", hints

    kept: list[str] = []
    used = _estimate_tokens(hints_text)
    for item in items[:max_history_items]:
        item_cost = _estimate_tokens(item)
        if used + item_cost > available:
            break
        kept.append(item)
        used += item_cost

    return "; ".join(kept), hints
