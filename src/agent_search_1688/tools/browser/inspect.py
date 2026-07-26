"""Playwright-backed navigation and text snapshots; no arbitrary JavaScript."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlparse

from ..registry import ToolEntry
from ..web.extract import _validate_public_url


class BrowserInspector:
    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._page = None
        self._console_messages: list[dict[str, str]] = []

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
            self._page.on("console", self._record_console_message)
            self._page.on("pageerror", self._record_page_error)
        response = self._page.goto(safe_url, wait_until="domcontentloaded", timeout=30_000)
        return {"url": self._page.url, "title": self._page.title(), "status": response.status if response else None}

    def snapshot(self, max_characters: int) -> dict[str, Any]:
        if self._page is None:
            raise ValueError("尚未导航页面，请先调用 browser_navigate")
        text = self._page.locator("body").inner_text(timeout=10_000)
        return {"url": self._page.url, "text": text[:max_characters], "truncated": len(text) > max_characters}

    def console(self, operation: str, max_links: int, clear: bool) -> dict[str, Any]:
        if self._page is None:
            raise ValueError("尚未导航页面，请先调用 browser_navigate")
        if operation == "links":
            links = self._page.locator("a").evaluate_all(
                "anchors => anchors.map(anchor => ({text: (anchor.innerText || anchor.textContent || '').trim(), href: anchor.href || ''}))"
            )
            public_links: list[dict[str, str]] = []
            seen: set[str] = set()
            for item in links:
                if not isinstance(item, dict):
                    continue
                href = item.get("href")
                text = item.get("text")
                if not isinstance(href, str) or not isinstance(text, str):
                    continue
                absolute_href = urljoin(self._page.url, href)
                parsed = urlparse(absolute_href)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc or absolute_href in seen:
                    continue
                seen.add(absolute_href)
                public_links.append({"text": text[:500], "href": absolute_href})
                if len(public_links) >= max_links:
                    break
            return {"url": self._page.url, "links": public_links, "truncated": len(public_links) >= max_links}
        result = {"url": self._page.url, "console_messages": list(self._console_messages)}
        if clear:
            self._console_messages.clear()
        return result

    def _record_console_message(self, message: Any) -> None:
        self._append_console_message("console", f"{message.type}: {message.text}")

    def _record_page_error(self, error: Any) -> None:
        self._append_console_message("pageerror", str(error))

    def _append_console_message(self, source: str, text: str) -> None:
        self._console_messages.append({"source": source, "text": text[:2_000]})
        del self._console_messages[:-100]


def register_browser_tools(registry, inspector: BrowserInspector) -> None:
    registry.register(ToolEntry("browser_navigate", "在项目受控浏览器中打开公开网页。", {"type": "object", "properties": {"url": {"type": "string", "minLength": 8, "maxLength": 2_000}}, "required": ["url"], "additionalProperties": False}, lambda arguments: inspector.navigate(_only_url(arguments))))
    registry.register(ToolEntry("browser_snapshot", "返回当前页面的文本快照，不执行任意 JavaScript。", {"type": "object", "properties": {"max_characters": {"type": "integer", "minimum": 500, "maximum": 30_000}}, "additionalProperties": False}, lambda arguments: inspector.snapshot(_max_characters(arguments))))
    registry.register(ToolEntry("browser_console", "读取当前页面的 console/JS 错误，或以只读方式列出页面锚点文本与 href。先调用 browser_navigate；不执行模型提供的任意 JavaScript。", {"type": "object", "properties": {"operation": {"type": "string", "enum": ["messages", "links"]}, "max_links": {"type": "integer", "minimum": 1, "maximum": 500}, "clear": {"type": "boolean"}}, "additionalProperties": False}, lambda arguments: inspector.console(*_console_arguments(arguments))))


def _only_url(arguments: dict[str, Any]) -> str:
    if set(arguments) != {"url"} or not isinstance(arguments["url"], str):
        raise ValueError("browser_navigate 只接受 url")
    return arguments["url"]


def _max_characters(arguments: dict[str, Any]) -> int:
    value = arguments.get("max_characters", 12_000)
    if set(arguments) - {"max_characters"} or not isinstance(value, int) or isinstance(value, bool) or not 500 <= value <= 30_000:
        raise ValueError("browser_snapshot 参数无效")
    return value


def _console_arguments(arguments: dict[str, Any]) -> tuple[str, int, bool]:
    if set(arguments) - {"operation", "max_links", "clear"}:
        raise ValueError("browser_console 参数无效")
    operation = arguments.get("operation", "messages")
    max_links = arguments.get("max_links", 100)
    clear = arguments.get("clear", False)
    if operation not in {"messages", "links"}:
        raise ValueError("browser_console.operation 必须为 messages 或 links")
    if not isinstance(max_links, int) or isinstance(max_links, bool) or not 1 <= max_links <= 500:
        raise ValueError("browser_console.max_links 必须在 1 到 500 之间")
    if not isinstance(clear, bool):
        raise ValueError("browser_console.clear 必须为布尔值")
    return operation, max_links, clear
