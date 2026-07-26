"""Playwright-backed navigation and text snapshots; no arbitrary JavaScript."""

from __future__ import annotations

from typing import Any

from ..registry import ToolEntry
from ..web.extract import _validate_public_url


class BrowserInspector:
    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._page = None

    def navigate(self, url: str) -> dict[str, Any]:
        safe_url = _validate_public_url(url)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ValueError("Browser 后端未安装；请安装 playwright 并执行 playwright install chromium") from exc
        if self._page is None:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            self._page = self._browser.new_page()
        response = self._page.goto(safe_url, wait_until="domcontentloaded", timeout=30_000)
        return {"url": self._page.url, "title": self._page.title(), "status": response.status if response else None}

    def snapshot(self, max_characters: int) -> dict[str, Any]:
        if self._page is None:
            raise ValueError("尚未导航页面，请先调用 browser_navigate")
        text = self._page.locator("body").inner_text(timeout=10_000)
        return {"url": self._page.url, "text": text[:max_characters], "truncated": len(text) > max_characters}


def register_browser_tools(registry, inspector: BrowserInspector) -> None:
    registry.register(ToolEntry("browser_navigate", "在项目受控浏览器中打开公开网页。", {"type": "object", "properties": {"url": {"type": "string", "minLength": 8, "maxLength": 2_000}}, "required": ["url"], "additionalProperties": False}, lambda arguments: inspector.navigate(_only_url(arguments))))
    registry.register(ToolEntry("browser_snapshot", "返回当前页面的文本快照，不执行任意 JavaScript。", {"type": "object", "properties": {"max_characters": {"type": "integer", "minimum": 500, "maximum": 30_000}}, "additionalProperties": False}, lambda arguments: inspector.snapshot(_max_characters(arguments))))


def _only_url(arguments: dict[str, Any]) -> str:
    if set(arguments) != {"url"} or not isinstance(arguments["url"], str):
        raise ValueError("browser_navigate 只接受 url")
    return arguments["url"]


def _max_characters(arguments: dict[str, Any]) -> int:
    value = arguments.get("max_characters", 12_000)
    if set(arguments) - {"max_characters"} or not isinstance(value, int) or isinstance(value, bool) or not 500 <= value <= 30_000:
        raise ValueError("browser_snapshot 参数无效")
    return value
