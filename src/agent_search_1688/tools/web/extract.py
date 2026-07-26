"""Bounded public-web text extraction."""

from __future__ import annotations

from html.parser import HTMLParser
import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse
import urllib.error
import urllib.request

from ...config import PurchaseConfig
from ..registry import ToolEntry


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored and data.strip():
            self.parts.append(data.strip())


def _validate_public_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("url 必须是公开 HTTP(S) 地址")
    if parsed.hostname.lower() == "localhost":
        raise ValueError("禁止访问本机地址")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("无法解析目标域名") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("禁止访问私有或保留网络地址")
    return parsed.geturl()


def web_extract_handler(arguments: dict[str, Any], config: PurchaseConfig) -> dict[str, Any]:
    if set(arguments) - {"url", "max_characters"}:
        raise ValueError("web_extract 不接受额外参数")
    url = arguments.get("url")
    maximum = arguments.get("max_characters", 12_000)
    if not isinstance(url, str) or not isinstance(maximum, int) or isinstance(maximum, bool) or not 500 <= maximum <= 30_000:
        raise ValueError("web_extract 参数格式无效")
    safe_url = _validate_public_url(url)
    request = urllib.request.Request(safe_url, headers={"User-Agent": "as1688/0.3.0", "Accept": "text/html,text/plain"})
    try:
        with urllib.request.urlopen(request, timeout=config.request_timeout_seconds) as response:
            raw = response.read(2_000_001)
            final_url = response.geturl()
            content_type = response.headers.get_content_type()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"网页提取失败：{exc}") from exc
    if len(raw) > 2_000_000:
        raise ValueError("网页响应过大")
    decoded = raw.decode("utf-8", errors="replace")
    if content_type == "text/html":
        parser = _TextExtractor()
        parser.feed(decoded)
        decoded = "\n".join(parser.parts)
    return {"url": final_url, "content": decoded[:maximum], "truncated": len(decoded) > maximum}


WEB_EXTRACT_SCHEMA: dict[str, Any] = {"type": "object", "properties": {"url": {"type": "string", "minLength": 8, "maxLength": 2_000}, "max_characters": {"type": "integer", "minimum": 500, "maximum": 30_000}}, "required": ["url"], "additionalProperties": False}


def build_web_extract_entry(config: PurchaseConfig) -> ToolEntry:
    return ToolEntry("web_extract", "提取公开网页的受限文本；不能访问内网、本机或证明登录后信息。", WEB_EXTRACT_SCHEMA, lambda arguments: web_extract_handler(arguments, config))
