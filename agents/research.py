"""
调研助手 Agent
==============
具备网络搜索、网页抓取能力的研究型 Agent。
"""

from core.agent import Agent, AgentConfig
from core.llm import LLMClient

# 系统提示词已集中管理（interview/prompts.py "agent_research"，版本化注册表）
from interview.prompts import RESEARCH_SYSTEM_PROMPT
from tools.file_ops import read_file, write_file
from tools.search import fetch_webpage, web_search


def create_research_agent(llm_client: LLMClient) -> Agent:
    """
    创建一个调研助手 Agent。

    Args:
        llm_client: LLM 客户端实例

    Returns:
        配置了搜索和文件工具的 Agent
    """
    config = AgentConfig(
        max_steps=20,
        max_tool_calls=15,
        temperature=0.5,
        system_prompt=RESEARCH_SYSTEM_PROMPT,
    )

    return Agent(
        llm_client=llm_client,
        tools=[web_search, fetch_webpage, write_file, read_file],
        config=config,
    )
