"""1688 Agent Search 的受控工具注册表。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ToolEntry:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    parallel_safe: bool = False

    def as_mcp_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, ToolEntry] = {}

    def register(self, entry: ToolEntry) -> None:
        if entry.name in self._entries:
            raise ValueError(f"工具名称重复：{entry.name}")
        self._entries[entry.name] = entry

    def definitions(self) -> list[dict[str, Any]]:
        return [entry.as_mcp_definition() for entry in self._entries.values()]

    def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        entry = self._entries.get(name)
        if entry is None:
            raise KeyError(f"未注册工具：{name}")
        return entry.handler(arguments)

    def is_parallel_safe(self, name: str) -> bool:
        entry = self._entries.get(name)
        return bool(entry and entry.parallel_safe)
