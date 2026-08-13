"""
上下文优化器 (v2 — Priority-based Hybrid Retention)
====================================================
从简单滑动窗口升级为「优先级混合保留」策略。

核心问题:
    简单滑动窗口的缺陷:
        - 无差别淘汰: 重要的 system prompt 和无关的闲聊一样对待
        - 无语义感知: 不知道哪些历史消息对当前任务有价值
        - 一刀切: 所有场景用同样的截断策略

优化方案 (本项目实现了 4 种):

    ┌──────────────────────────────────────────────────────┐
    │  1. Priority Scoring 优先级评分                      │
    │    每条消息根据角色、内容、位置计算"保留价值分"      │
    │    淘汰时从最低分开始移除                            │
    │                                                      │
    │  2. Semantic Chunking 语义分块                       │
    │    按对话语义边界切分（而不是按条数）                │
    │    保持每个 Q&A 对的完整性                           │
    │                                                      │
    │  3. Hybrid Retention 混合保留策略                    │
    │    = 最近 N 条 (Recency) + 最重要 K 条 (Importance)  │
    │    + System Prompt (Always Keep)                     │
    │                                                      │
    │  4. Adaptive Budget 自适应预算                       │
    │    简单任务: 少分配 token (留给更多对话历史)         │
    │    复杂任务: 多分配 token (保证思考空间)             │
    └──────────────────────────────────────────────────────┘

面试场景的特殊性:
    - System prompt 决定面试官行为，绝不能丢
    - JD 分析结果是出题依据，非常重要
    - 之前的 Q&A 可能需要回溯（面试官翻旧账）
    - 当前题目的上下文最重要

使用:
    opt = ContextOptimizer(max_tokens=8000)
    opt.add_system_prompt("你是一位面试官...")
    opt.add_message(question_msg, priority=Priority.HIGH)
    opt.add_message(answer_msg, priority=Priority.MEDIUM)
    messages = opt.optimize()  # 返回优化后的消息列表
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

import tiktoken

from core.llm import Message, Role
from utils.logger import get_logger

logger = get_logger(__name__)


# ── 优先级定义 ─────────────────────────────────────────────────

class Priority(IntEnum):
    """消息保留优先级（数值越大越重要）"""
    CRITICAL = 100     # 绝对不能丢: System Prompt
    VERY_HIGH = 80     # 非常重要: JD 分析、当前题目
    HIGH = 60          # 重要: 前一道 Q&A、追问历史
    MEDIUM = 40        # 中等: 更早的 Q&A
    LOW = 20           # 低: 暖场话术、过渡文本
    DISPOSABLE = 0     # 可丢弃: 已被总结的消息、纯粹确认

    @classmethod
    def for_role(cls, role: Role) -> Priority:
        """根据消息角色自动分配优先级"""
        mapping = {
            Role.SYSTEM: cls.CRITICAL,
            Role.USER: cls.HIGH,
            Role.ASSISTANT: cls.MEDIUM,
            Role.TOOL: cls.HIGH,  # 工具调用结果很重要
        }
        return mapping.get(role, cls.MEDIUM)


@dataclass
class ScoredMessage:
    """带优先级评分的消息"""
    message: Message
    priority: Priority = Priority.MEDIUM
    token_count: int = 0
    # 附加元信息
    is_system: bool = False
    is_current_question: bool = False
    segment_id: int = 0       # 所属对话段（语义分块用）
    created_step: int = 0     # 在哪一步创建的

    @property
    def score(self) -> int:
        """综合保留分数 — 越高越不会被淘汰"""
        return self.priority.value


# ── 对话段定义 ─────────────────────────────────────────────────

@dataclass
class DialogueSegment:
    """一个语义完整的对话段（如：一道题 + 追问 + 回答）"""
    segment_id: int
    messages: list[ScoredMessage] = field(default_factory=list)
    summary: str = ""  # 段摘要（如果被压缩）

    @property
    def token_count(self) -> int:
        return sum(m.token_count for m in self.messages)

    @property
    def is_compressed(self) -> bool:
        return bool(self.summary)


# ── 上下文优化器 ───────────────────────────────────────────────

class ContextOptimizer:
    """
    上下文优化器 — 优先级混合保留策略。

    核心算法:
        1. 给每条消息打分 (Priority Scoring)
        2. 按语义边界分块 (Semantic Chunking)
        3. 如果超出预算，按以下规则裁剪:
           a. 保留所有 CRITICAL 和 VERY_HIGH (system prompt + 当前题)
           b. 保留最近 N 个完整段 (Recency)
           c. 从剩余段中挑分数最高的 K 个 (Importance)
           d. 被裁剪的段 → 压缩为摘要 (可选)
        4. 自适应预算: 根据任务复杂度动态调整

    使用:
        opt = ContextOptimizer(max_tokens=8000)
        opt.add_system(system_prompt)

        # 逐轮添加
        opt.begin_question()
        opt.add(question_msg, Priority.HIGH)
        opt.add(answer_msg, Priority.MEDIUM)
        opt.end_question()

        # 获取优化后的消息
        optimized = opt.optimize()
    """

    def __init__(
        self,
        max_tokens: int = 8000,
        model: str = "gpt-4o",
        # 裁剪策略参数
        keep_recent_segments: int = 3,     # 保留最近 N 个完整段
        keep_top_by_score: int = 2,        # 从旧段中保留最高分的 K 个
        compress_old_segments: bool = True, # 是否压缩旧段
        # 自适应预算
        adaptive_budget: bool = True,
        budget_reserve: float = 0.15,       # 保留 15% 预算给回答
    ):
        self.max_tokens = max_tokens
        self.budget_reserve = budget_reserve
        self.keep_recent = keep_recent_segments
        self.keep_top = keep_top_by_score
        self.compress_old = compress_old_segments
        self.adaptive = adaptive_budget

        # Token 编码器
        try:
            self._encoder = tiktoken.encoding_for_model(model)
        except KeyError:
            self._encoder = tiktoken.get_encoding("cl100k_base")

        # 内部状态
        self._system_messages: list[ScoredMessage] = []
        self._segments: list[DialogueSegment] = []
        self._current_segment: DialogueSegment | None = None
        self._segment_counter: int = 0
        self._step_counter: int = 0

    # ── Public API ─────────────────────────────────────────

    @property
    def effective_budget(self) -> int:
        """有效 token 预算（扣除回答预留）"""
        return int(self.max_tokens * (1 - self.budget_reserve))

    def add_system(self, content: str) -> None:
        """添加 system prompt（CRITICAL 优先级）"""
        msg = Message(role=Role.SYSTEM, content=content)
        self._system_messages.append(ScoredMessage(
            message=msg,
            priority=Priority.CRITICAL,
            token_count=self._count_tokens(content),
            is_system=True,
        ))

    def add(self, message: Message, priority: Priority | None = None) -> None:
        """添加一条消息"""
        if priority is None:
            priority = Priority.for_role(message.role)

        scored = ScoredMessage(
            message=message,
            priority=priority,
            token_count=self._count_tokens(message.content),
            created_step=self._step_counter,
        )

        if self._current_segment is not None:
            scored.segment_id = self._current_segment.segment_id
            self._current_segment.messages.append(scored)
        else:
            # 不在段中的消息（如初始暖场）
            scored.segment_id = -1

        self._step_counter += 1

    def begin_segment(self) -> None:
        """开始一个新的对话段"""
        self._segment_counter += 1
        self._current_segment = DialogueSegment(
            segment_id=self._segment_counter,
        )

    def end_segment(self, summary: str = "") -> None:
        """结束当前对话段"""
        if self._current_segment and self._current_segment.messages:
            if summary:
                self._current_segment.summary = summary
            self._segments.append(self._current_segment)
        self._current_segment = None

    begin_question = begin_segment
    end_question = end_segment

    def optimize(self) -> list[Message]:
        """
        优化消息列表，确保不超过 token 预算。

        算法的 4 个阶段:
            1. 测量: 计算当前总 token
            2. 分层: 按优先级和 recency 分层
            3. 裁剪: 从最低优先级开始移除
            4. 压缩: 被移除的段转为摘要

        Returns:
            优化后的消息列表
        """
        # ── 阶段 1: 测量 ──
        total = self._total_tokens()
        if total <= self.effective_budget:
            return self._all_messages()

        # ── 阶段 2: 分层 ──
        # 不可触碰: system messages
        untouchable = list(self._system_messages)
        untouchable_tokens = sum(m.token_count for m in untouchable)

        # 当前正在进行的段
        current_seg_msgs = (
            self._current_segment.messages if self._current_segment else []
        )

        # 历史段（排除当前段）
        historical = [s for s in self._segments]

        budget = self.effective_budget - untouchable_tokens

        # ── 阶段 3: 裁剪 ──
        # 策略: 保留最近的 + 最重要的
        to_keep: list[ScoredMessage] = list(current_seg_msgs)

        if historical:
            # 最近 N 个段
            recent = historical[-self.keep_recent:] if self.keep_recent > 0 else []
            # 更老的段
            old = historical[:-self.keep_recent] if self.keep_recent > 0 else historical

            for seg in recent:
                to_keep.extend(seg.messages)

            # 老段中挑最高分的 K 个
            old_messages: list[ScoredMessage] = []
            for seg in old:
                old_messages.extend(seg.messages)
            old_messages.sort(key=lambda m: m.score, reverse=True)
            to_keep.extend(old_messages[:self.keep_top])

            # ── 阶段 4: 压缩 ──
            # 被裁剪的老段 → 生成摘要
            if self.compress_old and old:
                for seg in old:
                    if not seg.is_compressed:
                        seg.summary = self._build_segment_summary(seg)

            # 摘要消息
            summaries = []
            for seg in historical:
                if seg.summary:
                    summary_text = f"[历史对话摘要 段{seg.segment_id}]\n{seg.summary}"
                    summaries.append(ScoredMessage(
                        message=Message(role=Role.SYSTEM, content=summary_text),
                        priority=Priority.LOW,
                        token_count=self._count_tokens(summary_text),
                        is_system=True,
                    ))

            to_keep = summaries + to_keep

        # 如果还是超预算 → 硬截断
        while self._sum_tokens(untouchable + to_keep) > budget and to_keep:
            # 移除分数最低的非 CRITICAL 消息
            to_keep.sort(key=lambda m: m.score)
            removed = to_keep.pop(0)
            logger.debug(f"硬截断: 移除优先级={removed.priority.name} 消息")

        # ── 返回 ──
        return [m.message for m in untouchable + to_keep]

    def adaptive_rebudget(self, task_complexity: float) -> None:
        """
        自适应预算调整。

        Args:
            task_complexity: 任务复杂度 (0.0 - 1.0)
                0.0 = 很简单（如确认性问答）
                0.5 = 中等（如一般面试题评估）
                1.0 = 很复杂（如系统设计题的深度追问）
        """
        if not self.adaptive:
            return

        # 复杂任务 → 给输出留更多空间
        if task_complexity > 0.7:
            self.budget_reserve = 0.25  # 留 25%
        elif task_complexity > 0.3:
            self.budget_reserve = 0.15  # 默认
        else:
            self.budget_reserve = 0.10

        logger.debug(f"自适应预算: complexity={task_complexity:.1f}, reserve={self.budget_reserve:.0%}")

    # ── 统计信息 ───────────────────────────────────────────

    def stats(self) -> dict:
        """上下文统计"""
        total_tokens = self._total_tokens()
        return {
            "total_tokens": total_tokens,
            "budget": self.max_tokens,
            "effective_budget": self.effective_budget,
            "utilization": f"{total_tokens / self.max_tokens * 100:.1f}%",
            "segments": len(self._segments),
            "system_messages": len(self._system_messages),
            "current_segment_msgs": len(self._current_segment.messages) if self._current_segment else 0,
            "step_count": self._step_counter,
        }

    # ── Internal ───────────────────────────────────────────

    def _count_tokens(self, text: str) -> int:
        """计算文本 token 数"""
        if not text:
            return 0
        return len(self._encoder.encode(text))

    def _total_tokens(self) -> int:
        """计算当前总 token"""
        return self._sum_tokens(self._all_scored())

    def _sum_tokens(self, messages: list[ScoredMessage]) -> int:
        """计算一批消息的 token 数（含消息格式开销）"""
        total = 0
        for m in messages:
            total += m.token_count + 6  # 6 = 消息边框 token
        return total

    def _all_scored(self) -> list[ScoredMessage]:
        """所有带评分消息"""
        result = list(self._system_messages)
        for seg in self._segments:
            result.extend(seg.messages)
        if self._current_segment:
            result.extend(self._current_segment.messages)
        return result

    def _all_messages(self) -> list[Message]:
        """所有 Message 对象"""
        return [m.message for m in self._all_scored()]

    def _build_segment_summary(self, segment: DialogueSegment) -> str:
        """构建段摘要（非 LLM 版本 — 提取关键信息）"""
        if not segment.messages:
            return ""

        # 提取 USER 消息的前 100 字符作为摘要
        user_msgs = [
            m for m in segment.messages
            if m.message.role in (Role.USER,)
        ]
        if not user_msgs:
            return f"段 {segment.segment_id}: 包含 {len(segment.messages)} 条消息"

        first = user_msgs[0].message.content[:200]
        return f"Q{segment.segment_id}: {first}..."

    def reset(self) -> None:
        """重置优化器"""
        self._system_messages = []
        self._segments = []
        self._current_segment = None
        self._segment_counter = 0
        self._step_counter = 0


# ── 场景测试 ───────────────────────────────────────────────────

def demo_context_management():
    """演示上下文优化策略 — 无需 API，纯逻辑展示"""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    console.print("[bold]Context 管理优化演示[/bold]\n")

    opt = ContextOptimizer(max_tokens=2000)

    # 添加 system prompt
    opt.add_system("你是一位资深后端面试官。请根据JD对候选人进行面试。" * 5)

    # 模拟 8 轮 Q&A
    for i in range(8):
        opt.begin_segment()
        opt.add(
            Message(role=Role.USER,
                    content=f"[第{i+1}题] 请解释Python的GIL..." + "详细描述" * (i + 1)),
            priority=Priority.HIGH,
        )
        opt.add(
            Message(role=Role.ASSISTANT,
                    content="GIL是全局解释器锁，用于保证..." * (i // 3 + 1)),
            priority=Priority.MEDIUM,
        )
        opt.end_segment()

    # 优化前
    stats_before = opt.stats()

    # 优化
    optimized = opt.optimize()
    stats_after = opt.stats()

    # 对比
    table = Table(title="Context 优化效果对比")
    table.add_column("指标")
    table.add_column("优化前")
    table.add_column("优化后")

    table.add_row("总消息数", "18+", str(len(optimized)))
    table.add_row("总 Token", str(stats_before["total_tokens"]), str(stats_after["total_tokens"]))
    table.add_row("利用率", stats_before["utilization"], stats_after["utilization"])

    console.print(table)
    console.print("\n[dim]策略: 保留最近 3 段完整对话 + 历史段压缩为摘要[/dim]")


if __name__ == "__main__":
    demo_context_management()
