"""Hermes-style direct Codex Responses provider.

This module is the only place that knows the Codex ChatGPT OAuth wire
protocol.  The runtime owns tools; this provider only turns project messages
and function schemas into Responses requests.
"""

from __future__ import annotations

import base64
import fcntl
import json
from pathlib import Path
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

from ..config import PurchaseConfig
from ..models import (
    Message,
    ProviderRuntime,
    ProviderToolCall,
    ProviderTurnResult,
    PurchaseSession,
    TokenUsage,
)
from ..prompt_builder import PurchasePromptBuilder
from .codex import (
    PurchaseInvalidResponse,
    PurchaseProviderError,
    PurchaseProviderInterrupted,
)
from .openai import _iter_1688_openai_sse_events

CODEX_AUTH_PATH = Path.home() / ".codex" / "auth.json"
CODEX_AUTH_LOCK_PATH = Path.home() / ".codex" / "auth.json.as1688.lock"
CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"


def load_local_codex_chatgpt_auth() -> dict[str, str]:
    """Read the Codex CLI-owned OAuth store without ever logging token values."""
    try:
        if CODEX_AUTH_PATH.is_symlink():
            raise PurchaseProviderError("Codex 登录文件不能是符号链接")
        payload = json.loads(CODEX_AUTH_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PurchaseProviderError("未找到本机 Codex 登录。请先运行：codex login") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise PurchaseProviderError("本机 Codex 登录文件无效") from exc
    if not isinstance(payload, dict) or payload.get("auth_mode") != "chatgpt":
        raise PurchaseProviderError("当前 Codex 不是 ChatGPT 登录。请运行：codex login")
    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        raise PurchaseProviderError("本机 Codex 登录缺少 OAuth 凭据")
    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
        raise PurchaseProviderError("本机 Codex 登录缺少可用 OAuth 凭据")
    return {"access_token": access, "refresh_token": refresh}


def _codex_headers(access_token: str) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "codex_cli_rs/0.0.0 (1688 Agent Search)",
        "originator": "codex_cli_rs",
    }
    try:
        payload = access_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        account_id = claims.get("https://api.openai.com/auth", {}).get("chatgpt_account_id")
        if isinstance(account_id, str) and account_id:
            headers["ChatGPT-Account-ID"] = account_id
    except (IndexError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        pass
    return headers


def _refresh_local_codex_auth() -> dict[str, str]:
    """Refresh under a sidecar lock so we never reuse a rotated token."""
    CODEX_AUTH_LOCK_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with CODEX_AUTH_LOCK_PATH.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        credentials = load_local_codex_chatgpt_auth()
        encoded = urllib.parse.urlencode(
            {"grant_type": "refresh_token", "refresh_token": credentials["refresh_token"], "client_id": CODEX_OAUTH_CLIENT_ID}
        ).encode()
        request = urllib.request.Request(
            CODEX_OAUTH_TOKEN_URL,
            data=encoded,
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                refreshed = json.loads(response.read(100_001).decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise PurchaseProviderError("Codex 登录已失效，请运行：codex login") from exc
        access = refreshed.get("access_token") if isinstance(refreshed, dict) else None
        refresh = refreshed.get("refresh_token") if isinstance(refreshed, dict) else None
        if not isinstance(access, str) or not access:
            raise PurchaseProviderError("Codex 登录刷新未返回 access token")
        # Re-read while holding the lock; preserve all CLI-owned metadata.
        payload = json.loads(CODEX_AUTH_PATH.read_text(encoding="utf-8"))
        tokens = payload.get("tokens") if isinstance(payload, dict) else None
        if not isinstance(tokens, dict):
            raise PurchaseProviderError("Codex 登录文件在刷新期间变为无效")
        tokens["access_token"] = access
        tokens["refresh_token"] = refresh if isinstance(refresh, str) and refresh else credentials["refresh_token"]
        payload["last_refresh"] = int(time.time())
        temporary = CODEX_AUTH_PATH.with_suffix(".as1688.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(CODEX_AUTH_PATH)
        return {"access_token": tokens["access_token"], "refresh_token": tokens["refresh_token"]}


def _responses_tools(definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"type": "function", "name": item["name"], "description": item["description"], "parameters": item["inputSchema"]}
        for item in definitions
    ]


class CodexResponsesProviderAdapter:
    def __init__(self, provider_runtime: ProviderRuntime, config: PurchaseConfig, prompt_builder: PurchasePromptBuilder):
        self.provider_runtime = provider_runtime
        self.config = config
        self.prompt_builder = prompt_builder
        self.actual_model = provider_runtime.model
        self.thread_id: str | None = None
        self._instructions = ""
        self._interrupted = False

    def open_1688_purchase_session(self, session: PurchaseSession, history: list[Message]) -> str:
        del history
        self.thread_id = session.provider_thread_id or f"codex_local_{session.id}"
        self._instructions = "\n\n".join((
            self.prompt_builder.build_1688_purchase_base_instructions(),
            self.prompt_builder.build_1688_purchase_context(session_id=session.id, provider_runtime=self.provider_runtime),
        ))
        return self.thread_id

    def switch_1688_purchase_model(self, model: str) -> None:
        self.actual_model = model
        self.provider_runtime = ProviderRuntime(**{**self.provider_runtime.__dict__, "model": model})

    def interrupt_1688_model_reply(self) -> None:
        self._interrupted = True

    def close(self) -> None:
        return None

    def run_1688_model_turn(self, *, input_items: list[dict[str, Any]], tool_definitions: list[dict[str, Any]], on_stream_started: Callable[[], None], on_delta: Callable[[str], None]) -> ProviderTurnResult:
        if self._interrupted:
            self._interrupted = False
            raise PurchaseProviderInterrupted("用户已停止回复")
        payload: dict[str, Any] = {"model": self.provider_runtime.model, "instructions": self._instructions, "input": input_items, "store": False, "stream": True}
        tools = _responses_tools(tool_definitions)
        if tools:
            payload.update({"tools": tools, "tool_choice": "auto", "parallel_tool_calls": True})
        response_payload = self._request(payload)
        on_stream_started()
        output = response_payload.get("output")
        if not isinstance(output, list):
            raise PurchaseInvalidResponse("Codex Responses 未返回 output 列表")
        calls: list[ProviderToolCall] = []
        text_parts: list[str] = []
        normalized_items: list[dict[str, Any]] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "function_call":
                call_id, name, arguments = item.get("call_id"), item.get("name"), item.get("arguments", "{}")
                if not isinstance(call_id, str) or not isinstance(name, str) or not isinstance(arguments, str):
                    raise PurchaseInvalidResponse("Codex function_call 格式无效")
                try:
                    parsed = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise PurchaseInvalidResponse("Codex function_call 参数不是 JSON") from exc
                if not isinstance(parsed, dict):
                    raise PurchaseInvalidResponse("Codex function_call 参数必须是对象")
                calls.append(ProviderToolCall(call_id=call_id, name=name, arguments=parsed))
                normalized_items.append({"type": "function_call", "call_id": call_id, "name": name, "arguments": arguments})
            elif item_type == "message":
                normalized_items.append(item)
                for part in item.get("content", []):
                    if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                        text_parts.append(part["text"])
        content = "".join(text_parts)
        if content:
            on_delta(content)
        usage_data = response_payload.get("usage") if isinstance(response_payload.get("usage"), dict) else {}
        usage = TokenUsage(input_tokens=int(usage_data.get("input_tokens", 0) or 0), output_tokens=int(usage_data.get("output_tokens", 0) or 0), total_tokens=int(usage_data.get("total_tokens", 0) or 0))
        response_id = str(response_payload.get("id") or self.thread_id or "")
        return ProviderTurnResult(content=content, tool_calls=calls, response_items=normalized_items, usage=usage, actual_model=str(response_payload.get("model") or self.provider_runtime.model), response_id=response_id, provider_thread_id=response_id)

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(2):
            token = load_local_codex_chatgpt_auth()["access_token"]
            request = urllib.request.Request(CODEX_RESPONSES_URL, data=json.dumps(payload).encode("utf-8"), headers=_codex_headers(token), method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.config.request_timeout_seconds) as response:
                    completed: dict[str, Any] | None = None
                    output_items: list[dict[str, Any]] = []
                    text_deltas: list[str] = []
                    for event in _iter_1688_openai_sse_events(response):
                        event_type = event.get("type")
                        if event_type == "error":
                            raise PurchaseProviderError(str(event.get("message") or "Codex Responses 流返回错误"))
                        if event_type == "response.output_item.done" and isinstance(event.get("item"), dict):
                            output_items.append(event["item"])
                        elif event_type == "response.output_text.delta" and isinstance(event.get("delta"), str):
                            text_deltas.append(event["delta"])
                        elif event_type == "response.completed":
                            candidate = event.get("response")
                            if isinstance(candidate, dict):
                                completed = dict(candidate)
                                completed["output"] = output_items or [{
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [{"type": "output_text", "text": "".join(text_deltas)}],
                                }]
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 0:
                    _refresh_local_codex_auth()
                    continue
                try:
                    detail = exc.read(8_001).decode("utf-8", errors="replace")
                except OSError:
                    detail = ""
                try:
                    parsed_detail = json.loads(detail)
                    if isinstance(parsed_detail, dict):
                        message = (parsed_detail.get("error") or {}).get("message")
                        detail = message if isinstance(message, str) else json.dumps(parsed_detail, ensure_ascii=False)
                except json.JSONDecodeError:
                    pass
                suffix = f"：{detail[:500]}" if detail else ""
                raise PurchaseProviderError(
                    f"Codex Responses 请求失败（HTTP {exc.code}）{suffix}"
                ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise PurchaseProviderError(f"无法连接 Codex Responses：{exc}") from exc
            if completed is None:
                raise PurchaseInvalidResponse("Codex Responses 流未返回完成事件")
            return completed
        raise AssertionError("unreachable")
