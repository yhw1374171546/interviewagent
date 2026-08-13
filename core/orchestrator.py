"""
多智能体编排器
==============
支持多个 Agent 协作完成复杂任务。

编排模式:
- Sequential  : 顺序执行，前一个的输出作为后一个的输入
- Parallel     : 并行执行，汇总结果
- Debate       : 多个 Agent 辩论，选择最佳答案

使用示例:
    orchestrator = Orchestrator(llm_client)
    result = await orchestrator.run_sequential(
        agents=[coder_agent, reviewer_agent],
        task="实现一个二分查找函数",
    )
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum

from .agent import Agent, RunResult


class OrchestrationMode(str, Enum):
    SEQUENTIAL = "sequential"   # 串行
    PARALLEL = "parallel"       # 并行
    DEBATE = "debate"           # 辩论


@dataclass
class OrchestrationResult:
    """编排执行结果"""
    final_answer: str
    mode: OrchestrationMode
    agent_results: list[RunResult] = field(default_factory=list)
    total_tokens: int = 0


class Orchestrator:
    """
    多智能体编排器。

    管理多个 Agent 的协作流程：
    - 串行: Agent A → Agent B → Agent C
    - 并行: Agent A/B/C 同时执行，汇总结果
    - 辩论: 多个 Agent 独立回答 → 裁判 Agent 选择最佳答案
    """

    def __init__(self):
        self._agents: dict[str, Agent] = {}

    def register(self, name: str, agent: Agent) -> None:
        """注册一个命名的 Agent"""
        self._agents[name] = agent

    def unregister(self, name: str) -> None:
        """移除 Agent"""
        self._agents.pop(name, None)

    # ── 编排模式 ────────────────────────────────────

    async def run_sequential(
        self,
        task: str,
        agent_names: list[str],
    ) -> OrchestrationResult:
        """
        串行执行：每个 Agent 的输出是下一个 Agent 的输入。

        适用场景: 代码编写 → 代码审查 → 文档生成
        """
        results: list[RunResult] = []
        total_tokens = 0
        current_task = task

        for name in agent_names:
            agent = self._agents.get(name)
            if not agent:
                raise ValueError(f"未注册的 Agent: {name}")

            result = await agent.run(current_task)
            results.append(result)
            total_tokens += result.total_tokens
            current_task = result.answer  # 传递给下一个 Agent

        return OrchestrationResult(
            final_answer=current_task,
            mode=OrchestrationMode.SEQUENTIAL,
            agent_results=results,
            total_tokens=total_tokens,
        )

    async def run_parallel(
        self,
        task: str,
        agent_names: list[str],
        aggregator_prompt: str = "请综合以下多个角度的分析，给出一个统一的结论:",
    ) -> OrchestrationResult:
        """
        并行执行：多个 Agent 同时处理同一任务，最后汇总。

        适用场景: 多角度分析、方案对比
        """
        agents = []
        for name in agent_names:
            agent = self._agents.get(name)
            if not agent:
                raise ValueError(f"未注册的 Agent: {name}")
            agents.append((name, agent))

        # 并行运行
        async def _run(name: str, ag: Agent) -> tuple[str, RunResult]:
            return name, await ag.run(task)

        parallel_results = await asyncio.gather(
            *[_run(name, ag) for name, ag in agents],
            return_exceptions=True,
        )

        results: list[RunResult] = []
        perspectives = []
        total_tokens = 0

        for item in parallel_results:
            if isinstance(item, Exception):
                continue
            name, result = item
            results.append(result)
            total_tokens += result.total_tokens
            perspectives.append(f"[{name}] 的观点:\n{result.answer}")

        # 委托第一个 Agent 做汇总（或新建一个专门的 summary agent）
        summary_agent = agents[0][1]
        summary_task = f"{aggregator_prompt}\n\n" + "\n\n---\n\n".join(perspectives)
        summary_result = await summary_agent.run(summary_task)

        return OrchestrationResult(
            final_answer=summary_result.answer,
            mode=OrchestrationMode.PARALLEL,
            agent_results=results,
            total_tokens=total_tokens + summary_result.total_tokens,
        )

    async def run_debate(
        self,
        topic: str,
        debater_names: list[str],
        judge_name: str,
        rounds: int = 2,
    ) -> OrchestrationResult:
        """
        辩论模式：多方辩论 → 裁判打分 → 给出最终结论。

        适用场景: 关键决策、方案选型
        """
        judge = self._agents.get(judge_name)
        if not judge:
            raise ValueError(f"未注册的裁判 Agent: {judge_name}")

        debaters = []
        for name in debater_names:
            ag = self._agents.get(name)
            if not ag:
                raise ValueError(f"未注册的辩论 Agent: {name}")
            debaters.append((name, ag))

        total_tokens = 0

        # 第 1 轮：各方初始观点
        arguments: dict[str, str] = {}
        for name, ag in debaters:
            result = await ag.run(f"请就以下议题发表你的看法：{topic}")
            arguments[name] = result.answer
            total_tokens += result.total_tokens

        # 后续轮次：各方反驳
        for r in range(1, rounds):
            prev_round = arguments.copy()
            for name, ag in debaters:
                others = "\n".join(
                    f"{n}: {arg}" for n, arg in prev_round.items() if n != name
                )
                prompt = (
                    f"议题: {topic}\n\n"
                    f"其他参与者的观点:\n{others}\n\n"
                    f"请反驳上述观点，并补充你自己的论据。"
                )
                result = await ag.run(prompt)
                arguments[name] = result.answer
                total_tokens += result.total_tokens

        # 裁判裁决
        debate_record = "\n\n".join(
            f"【{name}】:\n{arg}" for name, arg in arguments.items()
        )
        judge_task = (
            f"以下是一场关于「{topic}」的辩论记录:\n\n"
            f"{debate_record}\n\n"
            f"请作为裁判，给出你的最终判断和理由。"
        )
        judge_result = await judge.run(judge_task)
        total_tokens += judge_result.total_tokens

        return OrchestrationResult(
            final_answer=judge_result.answer,
            mode=OrchestrationMode.DEBATE,
            agent_results=[judge_result],
            total_tokens=total_tokens,
        )

    @property
    def agent_names(self) -> list[str]:
        return list(self._agents.keys())
