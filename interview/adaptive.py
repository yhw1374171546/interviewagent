"""
自适应难度调节器
================
根据候选人已答表现动态调整题目难度，实现「答得好→升级、答得差→降级」的
个性化面试（D 组: Agent 特有深度 #2）。

设计:
    面试题单（plan）按 JD 一次性生成，但同类题型内可动态替换:
    next_question 时按已答平均分判断候选人水平，从题库「同类型 + 同技能池」
    候选里选难度更匹配的一题，替换计划中的当前题。

规则（确定性、可解释、可测试）:
    - 平均分 ≥ 8.0 且连续 2 题高分 → 难度 +1（挑战）
    - 平均分 < 5.0 → 难度 -1（安抚，避免挫败）
    - 其余 → 保持原难度（不折腾）
    - 难度钳制在 [1, 5]，且必须能找到同类候选才替换（找不到就不动）

使用:
    from interview.adaptive import compute_target_difficulty, pick_replacement
    target = compute_target_difficulty(scores, original_difficulty)
    replacement = pick_replacement(question, target, candidates)
"""

from __future__ import annotations

from .question_bank import BankQuestion, QuestionType


def compute_target_difficulty(
    scores: list[float],
    original_difficulty: int,
    upgrade_threshold: float = 8.0,
    downgrade_threshold: float = 5.0,
    streak: int = 2,
) -> int:
    """
    计算当前题的目标难度。

    Args:
        scores: 已答题目得分列表（空列表 = 第一题，不调整）
        original_difficulty: 原计划难度 (1-5)
        upgrade_threshold: 平均分 ≥ 此值触发升级
        downgrade_threshold: 平均分 < 此值触发降级
        streak: 连续高分几题才升级（避免单题高分就跳）

    Returns:
        目标难度 (1-5，钳制)，无调整时 = original_difficulty
    """
    if not scores:
        return original_difficulty

    avg = sum(scores) / len(scores)

    # 降级优先（候选人表现差时先安抚）
    if avg < downgrade_threshold:
        return max(1, original_difficulty - 1)

    # 升级需要「平均分达标 + 最近 streak 题都高分」双条件
    recent = scores[-streak:]
    if avg >= upgrade_threshold and len(recent) >= streak and all(s >= upgrade_threshold for s in recent):
        return min(5, original_difficulty + 1)

    return original_difficulty


def pick_replacement(
    original: BankQuestion,
    target_difficulty: int,
    candidates: list[BankQuestion],
    exclude_ids: set[str] | None = None,
) -> BankQuestion | None:
    """
    从同类候选里挑难度 = target 的一题替换。

    Args:
        original: 原计划题目（类型/技能锚点）
        target_difficulty: 目标难度
        candidates: 同类型候选池（题库里同类型的题）
        exclude_ids: 排除已用题目

    Returns:
        替换题（难度匹配且未用过）；找不到返回 None（保持原题）
    """
    if target_difficulty == original.difficulty:
        return None  # 难度没变，不需要换

    exclude = exclude_ids or set()
    for q in candidates:
        if q.id in exclude or q.id == original.id:
            continue
        if q.difficulty == target_difficulty:
            return q
    # 找不到精确难度 → 返回 None（保持原题），不将就换同难度/跨难度题：
    # 自适应调整的语义是「难度确实变了才换」，否则原题更稳。
    return None


def build_candidate_pool(
    bank: list[BankQuestion],
    qtype: QuestionType,
    skills: list[str],
) -> list[BankQuestion]:
    """
    构建某类型的候选池: 同类型优先 + 技能标签重叠排序。

    面试题单生成后，替换题必须「同类型」——难度调整不能换题型。
    技能标签重叠作为排序偏好（同技能的题优先替换），但**不作硬过滤**:
    题库标签覆盖有限（实测按 JD 技能硬过滤，多数类型候选池为空，
    导致自适应永远无法触发）。宁可换同类型不同技能的题，也不放弃调整。
    """
    skill_set = {s.lower() for s in skills}
    pool: list[BankQuestion] = []
    for q in bank:
        if q.type != qtype:
            continue
        q_tags = {t.lower() for t in (q.tags or [])}
        overlap = bool(q_tags & skill_set)
        pool.append((q, overlap))

    # 有技能重叠的排前面（更相关），其余兜底（保证候选池非空）
    pool.sort(key=lambda x: x[1], reverse=True)
    return [q for q, _ in pool]
