"""
编程助手 Agent
==============
具备代码执行和文件操作能力的编程 Agent。
"""

from core.agent import Agent, AgentConfig
from core.llm import LLMClient

# 系统提示词已集中管理（interview/prompts.py "agent_coder"，版本化注册表）
from interview.prompts import CODER_SYSTEM_PROMPT
from tools.code_exec import run_python
from tools.file_ops import read_file, write_file


def create_coder_agent(llm_client: LLMClient) -> Agent:
    """
    创建一个编程助手 Agent。

    Args:
        llm_client: LLM 客户端实例

    Returns:
        配置了代码执行和文件工具的 Agent
    """
    config = AgentConfig(
        max_steps=25,
        max_tool_calls=20,
        temperature=0.3,  # 编程任务温度低一些，更精确
        system_prompt=CODER_SYSTEM_PROMPT,
    )

    return Agent(
        llm_client=llm_client,
        tools=[run_python, read_file, write_file],
        config=config,
    )
