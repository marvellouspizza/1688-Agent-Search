"""本地 SearXNG HTTP 客户端。"""

from __future__ import annotations

import json
from time import monotonic
from typing import Any
from urllib.parse import urlencode, urlparse
import urllib.error
import urllib.request


class SearXNGError(RuntimeError):
    pass


def validate_searxng_base_url(value: str) -> str:
    url = value.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("searxng_base_url 必须是完整的 HTTP(S) 地址")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("searxng_base_url 不能包含路径、查询参数或片段")
    return url


def search_searxng(*, base_url: str, query: str, limit: int, timeout_seconds: int) -> dict[str, Any]:
    normalized_base_url = validate_searxng_base_url(base_url)
    normalized_query = query.strip()
    if not 2 <= len(normalized_query) <= 300:
        raise ValueError("query 长度必须在 2 到 300 个字符之间")
    if not 1 <= limit <= 20:
        raise ValueError("limit 必须在 1 到 20 之间")
    request = urllib.request.Request(f"{normalized_base_url}/search?" + urlencode({"q": normalized_query, "format": "json"}), headers={"Accept": "application/json", "User-Agent": "as1688/0.3.0"}, method="GET")
    started_at = monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(2_000_001)
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise SearXNGError("SearXNG 拒绝请求；请确认已启用 JSON 格式") from exc
        raise SearXNGError(f"SearXNG 请求失败（HTTP {exc.code}）") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SearXNGError(f"无法连接本地 SearXNG：{exc}") from exc
    if len(raw) > 2_000_000:
        raise SearXNGError("SearXNG 响应过大")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SearXNGError("SearXNG 没有返回有效 JSON；请启用 format=json") from exc
    return {"query": normalized_query, "results": parse_searxng_results(payload, limit), "meta": {"backend": "searxng", "duration_ms": round((monotonic() - started_at) * 1000)}}


def parse_searxng_results(payload: Any, limit: int) -> list[dict[str, str]]:
    raw_results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(raw_results, list):
        raise SearXNGError("SearXNG JSON 缺少 results 列表")
    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")) or url in seen_urls:
            continue
        seen_urls.add(url)
        title, content, engine = item.get("title"), item.get("content"), item.get("engine")
        results.append({"title": title.strip() if isinstance(title, str) else "", "url": url, "snippet": content.strip() if isinstance(content, str) else "", "engine": engine.strip() if isinstance(engine, str) else ""})
        if len(results) >= limit:
            break
    return results
