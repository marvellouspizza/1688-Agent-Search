"""将 1688 Agent Search 工具注册表暴露为 stdio MCP Server。"""

from __future__ import annotations

import json
import sys
from typing import Any

from .tools import ToolRegistry
from .web_search_tool import build_1688_tool_registry


MCP_PROTOCOL_VERSION = "2024-11-05"


def _response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle_mcp_message(message: dict[str, Any], registry: ToolRegistry) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return _response(
            request_id,
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "1688-tools", "version": "0.3.0"},
            },
        )
    if method == "tools/list":
        return _response(request_id, {"tools": registry.definitions()})
    if method == "tools/call":
        params = message.get("params")
        if not isinstance(params, dict):
            return _error(request_id, -32602, "tools/call 参数无效")
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return _error(request_id, -32602, "工具名称或参数无效")
        try:
            result = registry.dispatch(name, arguments)
        except (KeyError, ValueError) as exc:
            return _response(request_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        except Exception:
            return _response(request_id, {"content": [{"type": "text", "text": "web_search 执行失败"}], "isError": True})
        return _response(
            request_id,
            {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]},
        )
    return _error(request_id, -32601, f"不支持的 MCP 方法：{method}")


def run_1688_mcp_server() -> int:
    registry = build_1688_tool_registry()
    for raw_line in sys.stdin:
        try:
            message = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        response = handle_mcp_message(message, registry)
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0
