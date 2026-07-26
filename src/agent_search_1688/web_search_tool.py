"""Registry 中的唯一第一期工具：本地 SearXNG 网页搜索。"""

from __future__ import annotations

from typing import Any

from .config import load_1688_purchase_config
from .searxng import search_searxng
from .tools import ToolEntry, ToolRegistry


WEB_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 2, "maxLength": 300},
        "limit": {"type": "integer", "minimum": 1, "maximum": 20},
    },
    "required": ["query"],
    "additionalProperties": False,
}


def web_search_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    unknown_arguments = sorted(set(arguments) - {"query", "limit"})
    if unknown_arguments:
        raise ValueError("web_search 不接受额外参数：" + ", ".join(unknown_arguments))
    query = arguments.get("query")
    limit = arguments.get("limit", 10)
    if not isinstance(query, str) or isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("web_search 参数格式无效")
    config = load_1688_purchase_config()
    return search_searxng(
        base_url=config.searxng_base_url,
        query=query,
        limit=limit,
        timeout_seconds=config.searxng_timeout_seconds,
    )


def build_1688_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolEntry(
            name="web_search",
            description=(
                "使用本机 SearXNG 搜索公开网页。结果是搜索索引，不能证明"
                "商品库存、价格或商家资质已经核验。"
            ),
            input_schema=WEB_SEARCH_SCHEMA,
            handler=web_search_handler,
        )
    )
    return registry
