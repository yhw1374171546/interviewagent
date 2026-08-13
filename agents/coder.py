"""
编程助手 Agent
==============
具备代码执行和文件操作能力的编程 Agent。
"""

from core.agent import Agent, AgentConfig
from core.llm import LLMClient
from tools.code_exec import run_python
from tools.file_ops import read_file, write_file

CODER_SYSTEM_PROMPT = """你是一位专业的编程助手。你可以编写和测试代码来解决用户的问题。

工作流程:
1. 仔细理解用户的编程需求
2. 使用 read_file 了解现有代码结构
3. 编写解决方案代码
4. 使用 run_python 测试代码正确性
5. 使用 write_file 保存最终代码
6. 给出代码说明，包括:
   - 代码功能概述
   - 使用方法
   - 复杂度分析（如适用）
   - 边界情况说明

注意事项:
- 代码要清晰可读，有必要的注释
- 优先考虑代码的健壮性和可维护性
- 测试要覆盖正常情况和边界情况
- 用中文回答
"""


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
