"""
Agent 循环检测器
================
识别 ReAct Agent 陷入死循环（反复调用同一工具/同一参数、无进展），
在 max_steps 硬上限之前提前终止，省 token 且行为更可解释。

检测信号（确定性规则，0 成本）:
    1. 同工具重复 — 最近 N 次工具调用里同一工具出现 ≥ 阈值次
    2. 同工具同参数重复 — 同一工具 + 相同参数重复 ≥ 阈值次（更强信号）
    3. 观察重复 — 同一工具调用返回的观察结果相同（工具行为无变化）

用法:
    from core.loop_detector import LoopDetector
    detector = LoopDetector()
    status = detector.record("search", {"q": "x"})  # 每次工具调用后记录
    if status["loop_detected"]:
        # 终止或换策略, status["reason"] 解释原因
"""

from __future__ import annotations

import json
from collections import Counter, deque


class LoopDetector:
    """
    基于工具调用历史的循环检测。

    Args:
        same_tool_repeats: 同一工具连续出现几次触发（含不同参数）
        same_args_repeats: 同一工具+同参数出现几次触发（强信号）
        window: 只观察最近 N 次调用（窗口滑动，避免历史累积误报）
        observe_window: 观察结果去重窗口（同一结果反复出现也视为循环）
    """

    def __init__(
        self,
        same_tool_repeats: int = 3,
        same_args_repeats: int = 2,
        window: int = 6,
        observe_window: int = 3,
    ):
        self.same_tool_repeats = same_tool_repeats
        self.same_args_repeats = same_args_repeats
        self.window = window
        self.observe_window = observe_window
        # 最近调用历史: [{"tool": str, "args": dict, "observation": str}]
        self._history: deque[dict] = deque(maxlen=window)
        self._recent_observations: deque[str] = deque(maxlen=observe_window)

    def record(self, tool: str, args: dict, observation: str = "") -> dict:
        """
        记录一次工具调用，返回检测状态。

        Returns:
            {"loop_detected": bool, "reason": str, "tool": str, "count": int}
            loop_detected=False 时 reason 为空。
        """
        self._history.append({"tool": tool, "args": args, "observation": observation})
        if observation:
            self._recent_observations.append(observation)

        # 信号 1: 观察结果重复（最强信号 — 工具不同但结果相同 = 无新信息）
        if len(self._recent_observations) >= self.observe_window:
            if len(set(self._recent_observations)) == 1:
                return {
                    "loop_detected": True,
                    "reason": f"连续 {self.observe_window} 次工具返回相同结果（无新信息）",
                    "tool": tool,
                    "count": self.observe_window,
                }

        # 信号 2: 同工具+同参数重复（强信号，阈值低）
        key = json.dumps({"tool": tool, "args": args}, ensure_ascii=False, sort_keys=True)
        same_args = sum(1 for h in self._history if json.dumps(
            {"tool": h["tool"], "args": h["args"]}, ensure_ascii=False, sort_keys=True
        ) == key)
        if same_args >= self.same_args_repeats:
            return {
                "loop_detected": True,
                "reason": f"同一工具调用相同参数 {same_args} 次（{tool}）",
                "tool": tool,
                "count": same_args,
            }

        # 信号 3: 同一工具重复（不同参数也算，如反复搜不同关键词但无进展）
        tool_counts = Counter(h["tool"] for h in self._history)
        count = tool_counts.get(tool, 0)
        if count >= self.same_tool_repeats:
            return {
                "loop_detected": True,
                "reason": f"同一工具连续调用 {count} 次（{tool}）",
                "tool": tool,
                "count": count,
            }

        return {"loop_detected": False, "reason": "", "tool": tool, "count": count}

    def reset(self) -> None:
        """重置检测状态（新任务开始时调用）"""
        self._history.clear()
        self._recent_observations.clear()
