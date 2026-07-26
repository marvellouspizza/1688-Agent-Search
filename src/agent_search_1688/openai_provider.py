"""OpenAI API Provider：模型目录、Responses 请求与 SSE 流式解析。"""

from __future__ import annotations

from dataclasses import replace
import json
from typing import Any, Callable, Iterator
import urllib.error
import urllib.request

from .config import PurchaseConfig
from .models import (
    Message,
    ModelOption,
    ProviderRuntime,
    ProviderStreamResult,
    PurchaseSession,
    TokenUsage,
)
from .prompt_builder import PurchasePromptBuilder
from .provider import (
    PurchaseInvalidResponse,
    PurchaseProviderError,
    PurchaseProviderInterrupted,
)


OPENAI_TEXT_MODEL_PREFIXES = ("gpt-", "o1", "o3", "o4")
OPENAI_NON_TEXT_MARKERS = (
    "audio",
    "computer-use",
    "deep-research",
    "embedding",
    "image",
    "instruct",
    "moderation",
    "realtime",
    "search",
    "transcribe",
    "tts",
    "whisper",
)


def _openai_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "as1688/0.2.0",
    }


def _safe_1688_openai_http_error(error: urllib.error.HTTPError) -> str:
    if error.code == 401:
        return (
            "OpenAI API Key 无效或已失效。请运行："
            "as1688 provider --update-key"
        )
    if error.code == 429:
        return "OpenAI API 请求过于频繁、余额不足或额度已用完"
    try:
        payload = json.loads(error.read(16_384).decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        payload = None
    message = None
    if isinstance(payload, dict):
        detail = payload.get("error")
        if isinstance(detail, dict) and isinstance(detail.get("message"), str):
            message = detail["message"]
    suffix = f"：{message[:500]}" if message else ""
    return f"OpenAI API 请求失败（HTTP {error.code}）{suffix}"


def _is_1688_openai_text_model(model: str) -> bool:
    lowered = model.lower()
    return (
        lowered.startswith(OPENAI_TEXT_MODEL_PREFIXES)
        and not any(marker in lowered for marker in OPENAI_NON_TEXT_MARKERS)
    )


def list_1688_openai_models(
    provider_runtime: ProviderRuntime,
    *,
    timeout_seconds: int = 60,
) -> list[ModelOption]:
    api_key = provider_runtime.credential
    if not api_key:
        raise PurchaseProviderError("OpenAI API Key 尚未配置")
    request = urllib.request.Request(
        f"{provider_runtime.base_url}/models",
        headers=_openai_headers(api_key),
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(5_000_001)
    except urllib.error.HTTPError as exc:
        raise PurchaseProviderError(_safe_1688_openai_http_error(exc)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PurchaseProviderError(f"无法连接 OpenAI API：{exc}") from exc
    if len(raw) > 5_000_000:
        raise PurchaseInvalidResponse("OpenAI 模型目录响应过大")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PurchaseInvalidResponse("OpenAI 模型目录格式无效") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise PurchaseInvalidResponse("OpenAI 未返回模型目录")

    models: list[ModelOption] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model = item.get("id")
        if not isinstance(model, str) or not _is_1688_openai_text_model(model):
            continue
        owner = item.get("owned_by")
        models.append(
            ModelOption(
                model=model,
                display_name=model,
                description=(
                    f"OpenAI API 模型 · {owner}"
                    if isinstance(owner, str) and owner
                    else "OpenAI API 模型"
                ),
            )
        )
    models.sort(key=lambda option: option.model, reverse=True)
    if not models:
        raise PurchaseProviderError("当前 OpenAI 账号没有返回可用的文本模型")
    return models


def _iter_1688_openai_sse_events(response: Any) -> Iterator[dict[str, Any]]:
    data_lines: list[str] = []
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="strict").rstrip("\r\n")
        if not line:
            if data_lines:
                data = "\n".join(data_lines)
                data_lines.clear()
                if data != "[DONE]":
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise PurchaseInvalidResponse(
                            "OpenAI SSE 事件不是有效 JSON"
                        ) from exc
                    if not isinstance(event, dict):
                        raise PurchaseInvalidResponse("OpenAI SSE 事件格式无效")
                    yield event
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        raise PurchaseInvalidResponse("OpenAI SSE 流没有正常结束")


class OpenAIResponsesProviderAdapter:
    def __init__(
        self,
        provider_runtime: ProviderRuntime,
        config: PurchaseConfig,
        prompt_builder: PurchasePromptBuilder,
    ):
        if not provider_runtime.credential:
            raise PurchaseProviderError("OpenAI API Key 尚未配置")
        self.provider_runtime = provider_runtime
        self.config = config
        self.prompt_builder = prompt_builder
        self.actual_model = provider_runtime.model
        self.thread_id: str | None = None
        self.active_turn_id: str | None = None
        self._history: list[dict[str, str]] = []
        self._instructions = ""
        self._active_response: Any | None = None

    def open_1688_purchase_session(
        self,
        session: PurchaseSession,
        history: list[Message],
    ) -> str:
        self.thread_id = (
            session.provider_thread_id
            if session.provider == self.provider_runtime.provider
            and session.provider_thread_id
            else f"openai_local_{session.id}"
        )
        self._history = [
            {"role": message.role.value, "content": message.content}
            for message in history
        ]
        self._instructions = "\n\n".join(
            [
                self.prompt_builder.build_1688_purchase_base_instructions(),
                self.prompt_builder.build_1688_purchase_context(
                    session_id=session.id,
                    provider_runtime=self.provider_runtime,
                ),
            ]
        )
        return self.thread_id

    def switch_1688_purchase_model(self, model: str) -> None:
        self.provider_runtime = replace(self.provider_runtime, model=model)
        self.actual_model = model

    def stream_1688_model_reply(
        self,
        *,
        user_input: str,
        user_message_id: str,
        on_stream_started: Callable[[], None],
        on_delta: Callable[[str], None],
    ) -> ProviderStreamResult:
        del user_message_id
        if self.thread_id is None:
            raise PurchaseProviderError("Provider Session 尚未创建")
        api_key = self.provider_runtime.credential
        if not api_key:
            raise PurchaseProviderError("OpenAI API Key 尚未配置")
        request_payload = {
            "model": self.provider_runtime.model,
            "instructions": self._instructions,
            "input": [*self._history, {"role": "user", "content": user_input}],
            "stream": True,
            "store": False,
            "tools": [],
            "tool_choice": "none",
            "parallel_tool_calls": False,
        }
        request = urllib.request.Request(
            f"{self.provider_runtime.base_url}/responses",
            data=json.dumps(request_payload).encode("utf-8"),
            headers=_openai_headers(api_key),
            method="POST",
        )
        parts: list[str] = []
        completed_response: dict[str, Any] | None = None
        fallback_text: str | None = None
        try:
            self._active_response = urllib.request.urlopen(
                request,
                timeout=self.config.request_timeout_seconds,
            )
            on_stream_started()
            for event in _iter_1688_openai_sse_events(self._active_response):
                event_type = event.get("type")
                if event_type == "response.output_text.delta":
                    delta = event.get("delta")
                    if not isinstance(delta, str):
                        raise PurchaseInvalidResponse("OpenAI 文字增量格式无效")
                    parts.append(delta)
                    on_delta(delta)
                elif event_type == "response.refusal.delta":
                    delta = event.get("delta")
                    if not isinstance(delta, str):
                        raise PurchaseInvalidResponse("OpenAI 拒绝文字格式无效")
                    parts.append(delta)
                    on_delta(delta)
                elif event_type == "response.output_text.done":
                    text = event.get("text")
                    if isinstance(text, str):
                        fallback_text = text
                elif event_type == "response.refusal.done":
                    refusal = event.get("refusal")
                    if isinstance(refusal, str):
                        fallback_text = refusal
                elif event_type in {
                    "response.created",
                    "response.in_progress",
                    "response.queued",
                    "response.content_part.added",
                    "response.content_part.done",
                    "response.output_text.annotation.added",
                }:
                    continue
                elif event_type in {
                    "response.output_item.added",
                    "response.output_item.done",
                }:
                    item = event.get("item")
                    item_type = item.get("type") if isinstance(item, dict) else None
                    if item_type not in {"message", "reasoning"}:
                        raise PurchaseInvalidResponse(
                            f"普通对话拒绝 OpenAI 输出项：{item_type}"
                        )
                elif isinstance(event_type, str) and event_type.startswith(
                    "response.reasoning"
                ):
                    continue
                elif event_type == "response.completed":
                    response_value = event.get("response")
                    if not isinstance(response_value, dict):
                        raise PurchaseInvalidResponse("OpenAI 完成事件格式无效")
                    completed_response = response_value
                elif event_type in {"response.failed", "response.incomplete", "error"}:
                    detail = event.get("error")
                    message = (
                        detail.get("message")
                        if isinstance(detail, dict)
                        else None
                    )
                    raise PurchaseProviderError(message or "OpenAI 请求未成功完成")
                else:
                    raise PurchaseInvalidResponse(
                        f"收到尚未审计的 OpenAI 流事件：{event_type}"
                    )
        except KeyboardInterrupt as exc:
            self.interrupt_1688_model_reply()
            raise PurchaseProviderInterrupted("用户已中止模型请求") from exc
        except urllib.error.HTTPError as exc:
            raise PurchaseProviderError(_safe_1688_openai_http_error(exc)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PurchaseProviderError(f"无法连接 OpenAI API：{exc}") from exc
        finally:
            if self._active_response is not None:
                self._active_response.close()
                self._active_response = None

        if completed_response is None:
            raise PurchaseInvalidResponse("OpenAI 流缺少 response.completed")
        if completed_response.get("status") != "completed":
            raise PurchaseProviderError("OpenAI Response 未成功完成")
        content = "".join(parts) or fallback_text or ""
        if not content.strip():
            raise PurchaseInvalidResponse("OpenAI 已完成，但没有返回有效文字")
        actual_model = completed_response.get("model")
        if not isinstance(actual_model, str) or not actual_model:
            actual_model = self.provider_runtime.model
        usage_value = completed_response.get("usage")
        usage = usage_value if isinstance(usage_value, dict) else {}
        token_usage = TokenUsage(
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            total_tokens=int(usage.get("total_tokens", 0) or 0),
        )
        self._history.extend(
            [
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": content},
            ]
        )
        self.actual_model = actual_model
        return ProviderStreamResult(
            content=content,
            usage=token_usage,
            actual_model=actual_model,
            provider_thread_id=self.thread_id,
        )

    def interrupt_1688_model_reply(self) -> None:
        if self._active_response is not None:
            self._active_response.close()
            self._active_response = None

    def close(self) -> None:
        self.interrupt_1688_model_reply()
