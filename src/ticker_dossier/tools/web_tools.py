"""Web search and fetch tools used for market verification."""
from __future__ import annotations

from ticker_dossier.research.web import web_fetch, web_search
from ticker_dossier.runtime.tools import Tool
from ticker_dossier.security import guard_web_fetch, untrusted_block


def _web_search(query: str, limit: int = 5) -> str:
    return untrusted_block("WEB_SEARCH", query, web_search(query, limit))


def _web_fetch(url: str, max_chars: int = 4000) -> str:
    guard_web_fetch(url)
    return untrusted_block("WEB", url, web_fetch(url, max_chars))


web_search_tool = Tool(
    name="web_search",
    description="搜索公开网页，用于核验股票代码、上市状态、公告、新闻和数据来源。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "limit": {"type": "integer", "description": "返回结果数量，默认 5"},
        },
        "required": ["query"],
    },
    run=_web_search,
)

web_fetch_tool = Tool(
    name="web_fetch",
    description="抓取指定 URL，返回 HTTP 状态、标题、描述和正文摘要；遇到 WAF/JS 会明确标注。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要抓取的 http/https URL"},
            "max_chars": {"type": "integer", "description": "正文摘要最大字符数"},
        },
        "required": ["url"],
    },
    run=_web_fetch,
)


web_tools = [web_search_tool, web_fetch_tool]
