"""
网络搜索工具
============
提供网页搜索和内容抓取能力。
"""

from __future__ import annotations

import httpx

from .base import tool


@tool(
    name="web_search",
    description="搜索互联网获取最新信息。返回相关结果的标题、URL 和摘要。",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词",
            },
            "num": {
                "type": "integer",
                "description": "返回结果数量，默认 5",
            },
        },
        "required": ["query"],
    },
)
async def web_search(query: str, num: int = 5) -> str:
    """
    执行网页搜索。

    使用 DuckDuckGo 作为搜索引擎（免费，无需 API Key）。
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={
                    "q": query,
                    "format": "json",
                    "no_html": 1,
                    "skip_disambig": 1,
                },
            )
            data = resp.json()

            results = []
            # Abstract
            if data.get("AbstractText"):
                results.append(f"📌 摘要: {data['AbstractText']}")

            # Related Topics
            for topic in data.get("RelatedTopics", [])[:num]:
                if isinstance(topic, dict) and topic.get("Text"):
                    results.append(f"• {topic['Text']}")

            if not results:
                return f"未找到与 '{query}' 相关的结果。"

            return "\n".join(results)

    except Exception as e:
        return f"搜索失败: {e}"


@tool(
    name="fetch_webpage",
    description="抓取指定网页内容并提取文本",
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "要抓取的网页 URL",
            },
        },
        "required": ["url"],
    },
)
async def fetch_webpage(url: str) -> str:
    """抓取网页文本内容"""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; AgentBot/1.0)",
                },
            )

            # 简易 HTML 到文本（生产环境建议用 BeautifulSoup）
            text = resp.text

            # 移除 script 和 style
            import re
            text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text)

            # 截断过长内容
            max_len = 4000
            if len(text) > max_len:
                text = text[:max_len] + "..."

            return text.strip()

    except Exception as e:
        return f"抓取失败: {e}"
