"""
预置智能体
==========
开箱即用的智能体实现。
"""

from .coder import create_coder_agent
from .research import create_research_agent

__all__ = ["create_research_agent", "create_coder_agent"]
