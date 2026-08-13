"""
日志模块
========
统一日志配置，支持彩色输出和文件持久化。
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    获取一个配置好的 logger。

    Args:
        name: 模块名（通常传 __name__）
        level: 日志级别

    Returns:
        配置好的 Logger 实例
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    # 控制台 handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(_ColoredFormatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"))
    logger.addHandler(console)

    # 文件 handler（可选）
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    file_handler = logging.FileHandler(
        log_dir / f"agent_{datetime.now().strftime('%Y%m%d')}.log",
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    )
    logger.addHandler(file_handler)

    return logger


class _ColoredFormatter(logging.Formatter):
    """带颜色的日志格式化器（终端友好）"""

    COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        if color:
            record.levelname = f"{color}{record.levelname}{self.RESET}"
        return super().format(record)
