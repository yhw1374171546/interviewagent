"""
Agent 主循环
============
实现两种经典的 Agent 运行模式：

1. ReAct (Reasoning + Acting) — 思考 → 行动 → 观察 → 循环
2. Plan-Execute — 先制定计划 → 逐步执行 → 验证

核心流程:
    User Input → [Agent Loop] → Final Answer
                   ↑    ↓
               Think → Act → Observe

使用示例:
    agent = Agent(llm_client, tools=[search_tool, code_tool])
    response = await agent.run("帮我调研 Rust 在嵌入式领域的前景")
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from tools.base import ToolRegistry
from utils.logger import get_logger

from .llm import (
    LLMClient,
    Message,
    Role,
    ToolCall,
)

logger = get_logger(__name__)


# ── 类型定义 ──────────────────────────────────────────────────

class AgentState(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    ACTING = "acting"
    FINISHED = "finished"
    ERROR = "error"


@dataclass
class AgentStep:
    """单步执行记录，用于 trace 和调试"""
    step_num: int
    thought: str = ""
    action: str = ""
    observation: str = ""
    state: AgentState = AgentState.IDLE


@dataclass
class RunResult:
    """Agent 运行结果"""
    answer: str
    steps: list[AgentStep] = field(default_factory=list)
    total_tokens: int = 0
    tool_calls_count: int = 0


# ── Agent 配置 ────────────────────────────────────────────────

@dataclass
class AgentConfig:
    """Agent 行为配置"""
    max_steps: int = 15            # 最大推理步数
    max_tool_calls: int = 30       # 最大工具调用次数
    temperature: float = 0.7
    max_tokens: int = 4096
    verbose: bool = True           # 是否打印详细日志

    # ReAct 模式下的 System Prompt 模板
    system_prompt: str = (
        "你是一个智能助手，能够使用工具来完成任务。"
        "请按照以下模式工作：\n"
        "1. 分析用户的需求\n"
        "2. 如果需要工具，调用相应的工具\n"
        "3. 根据工具返回的结果给出最终答案\n"
        "请用中文回答。"
    )


# ── Agent 主类 ────────────────────────────────────────────────

class Agent:
    """
    AI Agent 主类。

    支持 ReAct 模式：
    - 接收用户输入
    - 循环：思考 → 调用工具 → 观察结果
    - 满足终止条件时输出最终答案
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tools: list[Callable] | None = None,
        config: AgentConfig | None = None,
    ):
        self.llm = llm_client
        self.registry = ToolRegistry()
        self.config = config or AgentConfig()

        if tools:
            for tool in tools:
                self.registry.register(tool)

        self._messages: list[Message] = []
        self._steps: list[AgentStep] = []
        self._total_tokens = 0
        self._tool_calls_count = 0

    # ── Public API ───────────────────────────────────────────

    async def run(self, user_input: str) -> RunResult:
        """
        运行 Agent。

        Args:
            user_input: 用户输入的自然语言指令

        Returns:
            RunResult: 包含最终答案和执行记录的完整结果
        """
        self._reset()

        # 初始化消息列表
        self._messages.append(self.llm.system_message(self.config.system_prompt))
        self._messages.append(self.llm.user_message(user_input))

        if self.config.verbose:
            logger.info(f"[Agent] 开始处理: {user_input[:100]}...")
            logger.info(f"[Agent] 可用工具: {self.registry.tool_names()}")

        # ── Agent 主循环 ──
        step_num = 0
        while step_num < self.config.max_steps:
            step_num += 1

            if self.config.verbose:
                logger.info(f"\n{'─'*40}\n  Step {step_num}\n{'─'*40}")

            # 1. Think — 调用 LLM
            response = await self.llm.chat(
                messages=self._messages,
                tools=self.registry.to_definitions() or None,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
            self._total_tokens += sum(response.usage.values())

            step = AgentStep(step_num=step_num, state=AgentState.THINKING)
            step.thought = response.content

            if self.config.verbose:
                logger.info(f"  💭 思考: {response.content[:80]}...")

            # 2. 判断: 是调用工具还是给出最终答案
            if response.content:
                self._messages.append(Message(role=Role.ASSISTANT, content=response.content))

            if not response.tool_calls:
                # 无工具调用 — 对话结束
                step.state = AgentState.FINISHED
                self._steps.append(step)

                if self.config.verbose:
                    logger.info("  ✅ 完成")

                return RunResult(
                    answer=response.content,
                    steps=self._steps,
                    total_tokens=self._total_tokens,
                    tool_calls_count=self._tool_calls_count,
                )

            # 3. Act — 执行工具调用
            observations = []
            for tc in response.tool_calls:
                if self._tool_calls_count >= self.config.max_tool_calls:
                    logger.warning(f"达到最大工具调用次数 {self.config.max_tool_calls}")
                    break

                result = await self._execute_tool(tc)
                observations.append(result)
                self._tool_calls_count += 1

            step.state = AgentState.ACTING
            step.action = json.dumps(
                [{"name": tc.name, "args": tc.arguments} for tc in response.tool_calls],
                ensure_ascii=False,
            )
            step.observation = "\n".join(observations)
            self._steps.append(step)

            if self.config.verbose:
                for obs in observations:
                    logger.info(f"  🔧 工具返回: {obs[:120]}...")

        # 达到最大步数 — 强制给出答案
        if self.config.verbose:
            logger.warning(f"达到最大步数 {self.config.max_steps}，强制总结")

        final_msg = self.llm.user_message(
            "你已经完成了多轮工具调用。请基于以上所有信息，给出一个完整的最终答案。"
        )
        self._messages.append(final_msg)

        response = await self.llm.chat(
            messages=self._messages,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        self._total_tokens += sum(response.usage.values())

        return RunResult(
            answer=response.content,
            steps=self._steps,
            total_tokens=self._total_tokens,
            tool_calls_count=self._tool_calls_count,
        )

    async def run_stream(self, user_input: str):
        """流式运行 Agent — 逐步 yield 中间状态"""
        self._reset()
        self._messages.append(self.llm.system_message(self.config.system_prompt))
        self._messages.append(self.llm.user_message(user_input))

        step_num = 0
        while step_num < self.config.max_steps:
            step_num += 1

            response = await self.llm.chat(
                messages=self._messages,
                tools=self.registry.to_definitions() or None,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
            self._total_tokens += sum(response.usage.values())

            step = AgentStep(
                step_num=step_num,
                thought=response.content,
                state=AgentState.THINKING,
            )
            yield step

            if response.content:
                self._messages.append(Message(role=Role.ASSISTANT, content=response.content))

            if not response.tool_calls:
                step.state = AgentState.FINISHED
                yield step
                return

            for tc in response.tool_calls:
                yield await self._execute_tool(tc)

    # ── Internal ─────────────────────────────────────────────

    async def _execute_tool(self, tool_call: ToolCall) -> str:
        """执行单个工具调用"""
        logger.info(f"  🔨 调用工具: {tool_call.name}({tool_call.arguments})")

        try:
            result = await self.registry.execute(tool_call.name, **tool_call.arguments)
            result_str = str(result)
        except Exception as e:
            result_str = f"错误: {type(e).__name__}: {e}"
            logger.error(f"  工具执行失败: {result_str}")

        # 将工具结果追加到对话中
        self._messages.append(Message(
            role=Role.TOOL,
            content=result_str,
            tool_call_id=tool_call.id,
        ))

        # 如果是 OpenAI 格式，需要追加 assistant tool_calls 消息
        self._messages.append(Message(
            role=Role.ASSISTANT,
            content="",
            tool_calls=[{
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.name,
                    "arguments": json.dumps(tool_call.arguments, ensure_ascii=False),
                },
            }],
        ))

        return result_str

    def _reset(self) -> None:
        """重置 Agent 状态"""
        self._messages = []
        self._steps = []
        self._total_tokens = 0
        self._tool_calls_count = 0

    # ── 属性 ─────────────────────────────────────────────────

    @property
    def history(self) -> list[Message]:
        """对话历史"""
        return self._messages

    @property
    def token_usage(self) -> int:
        """累计 Token 消耗"""
        return self._total_tokens
