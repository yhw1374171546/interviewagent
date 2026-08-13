"""
文件操作工具
============
提供安全的文件读写能力。
"""

from __future__ import annotations

from pathlib import Path

from .base import tool

# 安全工作目录
SAFE_ROOT = Path.cwd()


@tool(
    name="read_file",
    description="读取文件内容。支持文本文件（.py, .md, .txt, .json 等）。",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "相对于项目根目录的文件路径",
            },
            "max_lines": {
                "type": "integer",
                "description": "最多读取行数，默认 200",
            },
        },
        "required": ["path"],
    },
)
async def read_file(path: str, max_lines: int = 200) -> str:
    """安全读取文件"""
    full_path = (SAFE_ROOT / path).resolve()

    # 安全检查
    if not str(full_path).startswith(str(SAFE_ROOT.resolve())):
        return "❌ 安全限制: 不允许访问工作目录外的文件"

    if not full_path.exists():
        return f"❌ 文件不存在: {path}"

    if full_path.is_dir():
        # 列出目录内容
        items = []
        for item in sorted(full_path.iterdir()):
            prefix = "📁" if item.is_dir() else "📄"
            items.append(f"{prefix} {item.name}")
        return "\n".join(items[:50])

    try:
        content = full_path.read_text(encoding="utf-8")
        lines = content.split("\n")
        total = len(lines)

        if total <= max_lines:
            return content

        # 截断并提示
        truncated = "\n".join(lines[:max_lines])
        return f"{truncated}\n\n... [截断: 共 {total} 行，显示前 {max_lines} 行]"

    except UnicodeDecodeError:
        return "❌ 无法以 UTF-8 编码读取该文件（可能是二进制文件）"
    except Exception as e:
        return f"❌ 读取失败: {e}"


@tool(
    name="write_file",
    description="写入内容到文件。会覆盖已有文件。",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "相对于项目根目录的文件路径",
            },
            "content": {
                "type": "string",
                "description": "要写入的文本内容",
            },
        },
        "required": ["path", "content"],
    },
)
async def write_file(path: str, content: str) -> str:
    """安全写入文件"""
    full_path = (SAFE_ROOT / path).resolve()

    if not str(full_path).startswith(str(SAFE_ROOT.resolve())):
        return "❌ 安全限制: 不允许写入工作目录外的文件"

    try:
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        return f"✅ 已写入: {path} ({len(content)} 字符)"
    except Exception as e:
        return f"❌ 写入失败: {e}"
