"""
全局配置
========
所有配置项集中管理，支持环境变量覆盖。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _load_pricing() -> dict:
    """模型价格表（元/百万 token，[输入, 输出]），可用 LLM_PRICING 环境变量覆盖"""
    defaults = {
        "deepseek-v4-flash": [0.2, 1.0],
        "deepseek-v4-pro": [1.0, 4.0],
        "gpt-4o": [17.8, 71.2],
        "gpt-4o-mini": [1.1, 4.4],
        "claude-sonnet-5": [21.4, 107.0],
    }
    override = os.getenv("LLM_PRICING")
    if override:
        try:
            defaults.update(json.loads(override))
        except json.JSONDecodeError:
            pass
    return defaults

load_dotenv()


@dataclass
class Settings:
    """Agent 全局配置"""

    # ── LLM ──────────────────────────────────────────
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o")
    # 快速模型（模型路由）: 评估/JD解析/暖场/出题等高频调用用快模型，
    # 最终报告用主模型。未配置时回退到主模型。
    # 实测: deepseek-v4-flash 7.8s vs v4-pro 17.6s（同场景 2.3 倍差）
    llm_fast_model: str = os.getenv("LLM_FAST_MODEL", "")
    llm_api_key: str = os.getenv("OPENAI_API_KEY", "")
    llm_base_url: str | None = os.getenv("LLM_BASE_URL") or None
    llm_temperature: float = 0.7
    llm_max_tokens: int = 4096

    # 模型价格表 (元 / 百万 token) — 用于成本估算（可观测性指标）。
    # 价格会随供应商调整，通过环境变量 JSON 覆盖，例如:
    #   LLM_PRICING='{"deepseek-v4-flash": [0.2, 1.0]}'
    llm_pricing: dict = field(default_factory=lambda: _load_pricing())

    # ── Anthropic ────────────────────────────────────
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")

    # ── Agent ────────────────────────────────────────
    agent_max_steps: int = 15
    agent_max_tool_calls: int = 30
    agent_verbose: bool = True

    # ── Memory ───────────────────────────────────────
    memory_max_tokens: int = 8000
    memory_persist_dir: str = "./data/chroma"
    memory_embedding_model: str = "all-MiniLM-L6-v2"

    # ── Logging ──────────────────────────────────────
    log_level: str = "INFO"
    log_dir: str = "./logs"

    # ── Paths ────────────────────────────────────────
    project_root: Path = field(default_factory=lambda: Path(__file__).parent.parent.resolve())

    def model_post_init(self):
        """初始化后创建必要的目录"""
        Path(self.log_dir).mkdir(exist_ok=True)
        Path(self.memory_persist_dir).mkdir(parents=True, exist_ok=True)


# 全局单例
settings = Settings()
