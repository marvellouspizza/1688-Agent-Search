"""Registry 中的第一期工具：本地 SearXNG 网页搜索。"""

from __future__ import annotations

from typing import Any

from ...config import load_1688_purchase_config
from ...skills import SkillCatalog
from .extract import build_web_extract_entry
from ..browser.inspect import BrowserInspector, register_browser_tools
from ..registry import ToolEntry, ToolRegistry
from .searxng import search_searxng


WEB_SEARCH_SCHEMA: dict[str, Any] = {"type": "object", "properties": {"query": {"type": "string", "minLength": 2, "maxLength": 300}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, "required": ["query"], "additionalProperties": False}


def web_search_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    unknown_arguments = sorted(set(arguments) - {"query", "limit"})
    if unknown_arguments:
        raise ValueError("web_search 不接受额外参数：" + ", ".join(unknown_arguments))
    query, limit = arguments.get("query"), arguments.get("limit", 10)
    if not isinstance(query, str) or isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("web_search 参数格式无效")
    config = load_1688_purchase_config()
    return search_searxng(base_url=config.searxng_base_url, query=query, limit=limit, timeout_seconds=config.searxng_timeout_seconds)


def build_1688_tool_registry(*, skill_root=None) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolEntry(name="web_search", description="使用本机 SearXNG 搜索公开网页。结果是搜索索引，不能证明商品库存、价格或商家资质已经核验。", input_schema=WEB_SEARCH_SCHEMA, handler=web_search_handler, parallel_safe=True))
    config = load_1688_purchase_config()
    registry.register(build_web_extract_entry(config))
    register_browser_tools(registry, BrowserInspector())
    catalog = SkillCatalog([skill_root] if skill_root is not None else [])
    registry.register(ToolEntry("skills_list", "列出本项目已安装的 Skill；不读取本机 Codex Skill。", {"type": "object", "properties": {}, "additionalProperties": False}, lambda arguments: _skills_list(arguments, catalog), parallel_safe=True))
    registry.register(ToolEntry("skill_view", "读取一个项目 Skill 或其 references 中的文件。", {"type": "object", "properties": {"name": {"type": "string"}, "path": {"type": "string"}}, "required": ["name"], "additionalProperties": False}, lambda arguments: _skill_view(arguments, catalog), parallel_safe=True))
    return registry


def _skills_list(arguments: dict[str, Any], catalog: SkillCatalog) -> dict[str, Any]:
    if arguments:
        raise ValueError("skills_list 不接受参数")
    return {"skills": [{"name": entry.name, "description": entry.description} for entry in catalog.list()]}


def _skill_view(arguments: dict[str, Any], catalog: SkillCatalog) -> dict[str, Any]:
    if set(arguments) - {"name", "path"} or not isinstance(arguments.get("name"), str):
        raise ValueError("skill_view 参数无效")
    path = arguments.get("path")
    if path is not None and not isinstance(path, str):
        raise ValueError("skill_view.path 必须是字符串")
    return {"name": arguments["name"], "path": path or "SKILL.md", "content": catalog.read(arguments["name"], path)}
