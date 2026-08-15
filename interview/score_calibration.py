"""
评分校准器
==========
解决真实评测发现的「LLM 对高分档系统性低估」（人工均分 8.6 vs LLM 6.4，
MAE 2.17）——LLM 对"内容扎实但不够炫"的回答给分保守。

校准思路（确定性规则，用已有信号，不额外调 LLM）:
    LLM 评分应与「关键词命中率」（回答覆盖要点的客观程度）大致一致:
        - 命中率高但 LLM 给分低 → 低估信号 → 校准加分
        - 命中率低但 LLM 给分高 → 高估信号 → 校准减分
    校准幅度 = 命中率与评分的「不一致程度」，封顶 ±2 分，钳制 [1,10]。

为什么不直接让 LLM 校准:
    校准是低频确定性决策（命中率是客观事实），规则 0 成本、可解释、
    可测试；LLM 校准 = 又一次主观评分，可能引入新偏差。

用法:
    from interview.score_calibration import calibrate_score
    score, meta = calibrate_score(llm_score=6, match_rate=0.8)
    # score=8, meta={"adjusted": True, "direction": "up", "amount": 2, "reason": "..."}
"""

from __future__ import annotations

# 命中率 → 期望评分的映射（校准基准）:
# 命中 100% 要点 → 期望 9 分；0% → 3 分（与 evaluator 的 _rate_from_match 一致）
# 校准只纠偏「命中率与评分的明显不一致」，不是重打分。
_MIN_SCORE = 3
_MAX_SCORE = 9
_MAX_ADJUST = 2  # 单次校准幅度上限


def _expected_score(match_rate: float) -> float:
    """按命中率估算期望评分（与 evaluator 的 3-9 分映射对齐）"""
    return _MIN_SCORE + match_rate * (_MAX_SCORE - _MIN_SCORE)


def calibrate_score(
    llm_score: int,
    match_rate: float | None,
    max_adjust: int = _MAX_ADJUST,
    low_match_threshold: float = 0.3,
    high_match_threshold: float = 0.7,
) -> tuple[int, dict]:
    """
    校准 LLM 评分。

    Args:
        llm_score: LLM 评估给出的分数（correctness/depth/structure 任一）
        match_rate: 关键词命中率（0-1）。None 表示无法判定（不校准）
        max_adjust: 最大校准幅度
        low_match_threshold: 命中率低于此值视为"内容未覆盖要点"
        high_match_threshold: 命中率高于此值视为"内容扎实覆盖要点"

    Returns:
        (校准后分数, 校准详情)
        meta = {"adjusted": bool, "direction": "up"|"down"|"none",
                "amount": int, "reason": str}
    """
    if match_rate is None:
        return llm_score, {"adjusted": False, "direction": "none", "amount": 0, "reason": ""}

    expected = _expected_score(match_rate)
    gap = expected - llm_score

    # 低估: 内容扎实（命中率高）但 LLM 给分明显偏低
    if match_rate >= high_match_threshold and gap >= 1.5:
        amount = min(int(gap), max_adjust)
        score = max(1, min(10, llm_score + amount))
        return score, {
            "adjusted": True, "direction": "up", "amount": amount,
            "reason": f"命中率 {match_rate:.0%} 但仅 {llm_score} 分（期望≈{expected:.0f}），校准 +{amount}",
        }

    # 高估: 内容没覆盖要点（命中率低）但 LLM 给分明显偏高
    if match_rate <= low_match_threshold and gap <= -1.5:
        amount = min(int(-gap), max_adjust)
        score = max(1, min(10, llm_score - amount))
        return score, {
            "adjusted": True, "direction": "down", "amount": amount,
            "reason": f"命中率 {match_rate:.0%} 却给 {llm_score} 分（期望≈{expected:.0f}），校准 -{amount}",
        }

    return llm_score, {"adjusted": False, "direction": "none", "amount": 0, "reason": ""}
