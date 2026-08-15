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
        fail_repeats: int = 3,
    ):
        self.same_tool_repeats = same_tool_repeats
        self.same_args_repeats = same_args_repeats
        self.window = window
        self.observe_window = observe_window
        self.fail_repeats = fail_repeats
        # 最近调用历史: [{"tool": str, "args": dict, "observation": str}]
        self._history: deque[dict] = deque(maxlen=window)
        self._recent_observations: deque[str] = deque(maxlen=observe_window)

    def record(self, tool: str, args: dict, observation: str = "", failed: bool = False) -> dict:
        """
        记录一次工具调用，返回检测状态。

        Args:
            tool: 工具名
            args: 工具参数
            observation: 工具返回结果
            failed: 该调用是否执行失败（抛异常）。失败重试是合法行为，
                不应计入循环检测——只有「成功但无进展」的重复才算循环。

        Returns:
            {"loop_detected": bool, "reason": str, "tool": str, "count": int}
            loop_detected=False 时 reason 为空。
        """
        self._history.append({"tool": tool, "args": args, "observation": observation})
        if observation:
            self._recent_observations.append(observation)

        # 信号 0: 连续失败重试（失败 N 次仍不放弃 = 无进展，也应兜底终止。
        # 单次失败重试是合法的，但连续失败说明工具不可用，继续重试无意义）
        if failed:
            fail_streak = 0
            for h in reversed(self._history):
                if str(h.get("observation", "")).startswith("错误:"):
                    fail_streak += 1
                else:
                    break
            if fail_streak >= self.fail_repeats:
                return {
                    "loop_detected": True,
                    "reason": f"同一工具连续失败 {fail_streak} 次（{tool}，服务可能不可用）",
                    "tool": tool,
                    "count": fail_streak,
                }
            return {"loop_detected": False, "reason": "", "tool": tool, "count": 0}

        # 信号 1: 观察结果重复（最强信号 — 工具不同但结果相同 = 无新信息）。
        # 失败调用的错误文本不计入（否则连续失败会误报"相同结果"）
        if len(self._recent_observations) >= self.observe_window:
            success_obs = [
                o for o in self._recent_observations
                if not str(o).startswith("错误:")
            ]
            if success_obs and len(success_obs) >= self.observe_window and len(set(success_obs)) == 1:
                return {
                    "loop_detected": True,
                    "reason": f"连续 {self.observe_window} 次工具返回相同结果（无新信息）",
                    "tool": tool,
                    "count": self.observe_window,
                }

        # 信号 2: 同工具+同参数重复（强信号，阈值低）— 只统计成功调用，
        # 失败调用不计入（失败→重试是合理行为）
        key = json.dumps({"tool": tool, "args": args}, ensure_ascii=False, sort_keys=True)
        same_args = sum(
            1 for h in self._history
            if not str(h.get("observation", "")).startswith("错误:")
            and json.dumps({"tool": h["tool"], "args": h["args"]}, ensure_ascii=False, sort_keys=True) == key
        )
        if same_args >= self.same_args_repeats:
            return {
                "loop_detected": True,
                "reason": f"同一工具调用相同参数 {same_args} 次（{tool}）",
                "tool": tool,
                "count": same_args,
            }

        # 信号 3: 同一工具重复（不同参数也算，如反复搜不同关键词但无进展）—
        # 只统计成功调用
        tool_counts = Counter(
            h["tool"] for h in self._history
            if not str(h.get("observation", "")).startswith("错误:")
        )
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
