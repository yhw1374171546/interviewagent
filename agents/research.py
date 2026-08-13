"""
调研助手 Agent
==============
具备网络搜索、网页抓取能力的研究型 Agent。
"""

from core.agent import Agent, AgentConfig
from core.llm import LLMClient
from tools.file_ops import read_file, write_file
from tools.search import fetch_webpage, web_search

RESEARCH_SYSTEM_PROMPT = """你是一位专业的研究助理。你的任务是帮助用户进行深入调研并提供全面、准确的分析报告。

工作流程:
1. 充分理解用户的调研问题
2. 使用 web_search 工具搜索相关信息
3. 如果搜索结果不够详细，使用 fetch_webpage 获取具体页面内容
4. 交叉验证多个来源的信息
5. 使用 write_file 工具将调研报告保存为 Markdown 文件
6. 给出结构化的最终答案，包含:
   - 核心发现
   - 不同角度的观点
   - 信息来源引用

注意事项:
- 始终标注信息来源
- 对不确定的信息要说明
- 用中文回答
"""


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
