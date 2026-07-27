"""供应商解析，以及本机 Codex ChatGPT Provider。"""

from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

from ..config import (
    CODEX_PROVIDER,
    CODEX_RUNTIME_APP_SERVER,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    MODEL_ENV,
    OPENAI_PROVIDER,
    PROVIDER_ENV,
    PurchaseConfig,
    SUPPORTED_PROVIDERS,
)
from ..credentials import load_1688_openai_api_key
from ..models import (
    Message,
    ModelOption,
    ProviderRuntime,
    ProviderStreamResult,
    PurchaseSession,
    TokenUsage,
)
from ..prompt_builder import PurchasePromptBuilder


class PurchaseProviderError(RuntimeError):
    pass


class PurchaseProviderInterrupted(PurchaseProviderError):
    pass


class PurchaseInvalidResponse(PurchaseProviderError):
    pass


MINIMUM_CODEX_VERSION = (0, 125, 0)

_CODEX_SUBPROCESS_ALWAYS_STRIP = {
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GITHUB_APP_ID",
    "GITHUB_APP_PRIVATE_KEY_PATH",
    "GITHUB_APP_INSTALLATION_ID",
    "TELEGRAM_BOT_TOKEN",
    "DISCORD_BOT_TOKEN",
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "SLACK_SIGNING_SECRET",
    "HASS_TOKEN",
    "EMAIL_PASSWORD",
    "MODAL_TOKEN_ID",
    "MODAL_TOKEN_SECRET",
    "DAYTONA_API_KEY",
}


def _build_1688_codex_subprocess_environment() -> dict[str, str]:
    """沿用 Hermes 的 model-driving subprocess 秘密过滤边界。"""

    environment = os.environ.copy()
    for key in _CODEX_SUBPROCESS_ALWAYS_STRIP:
        environment.pop(key, None)
    for key in list(environment):
        upper = key.upper()
        if upper.startswith("AUXILIARY_") and upper.endswith(
            ("_API_KEY", "_BASE_URL")
        ):
            environment.pop(key, None)
        elif upper.startswith("GATEWAY_RELAY_") and upper.endswith(
            ("_SECRET", "_KEY", "_TOKEN")
        ):
            environment.pop(key, None)
    environment.pop("VIRTUAL_ENV", None)
    environment.pop("CONDA_PREFIX", None)
    environment.setdefault("PYTHONUTF8", "1")
    environment["RUST_LOG"] = "error"
    return environment


def _verify_1688_codex_version(codex_path: str) -> None:
    try:
        version_result = subprocess.run(
            [codex_path, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PurchaseProviderError("无法确认 Codex CLI 版本") from exc
    match = re.search(
        r"(\d+)\.(\d+)\.(\d+)",
        f"{version_result.stdout} {version_result.stderr}",
    )
    if version_result.returncode != 0 or match is None:
        raise PurchaseProviderError("无法确认 Codex CLI 版本")
    installed = tuple(int(part) for part in match.groups())
    if installed < MINIMUM_CODEX_VERSION:
        minimum = ".".join(str(part) for part in MINIMUM_CODEX_VERSION)
        current = ".".join(str(part) for part in installed)
        raise PurchaseProviderError(
            f"Codex CLI 版本过旧：{current}；最低需要：{minimum}"
        )


def resolve_1688_purchase_provider(
    config: PurchaseConfig,
    *,
    cli_model: str | None = None,
    cli_provider: str | None = None,
    environ: dict[str, str] | None = None,
    credential_override: str | None = None,
) -> ProviderRuntime:
    """固定优先级：CLI → config → 环境变量 → Provider 默认值。"""

    environment = environ if environ is not None else os.environ
    provider = (
        cli_provider
        or config.provider
        or environment.get(PROVIDER_ENV)
        or DEFAULT_PROVIDER
    )
    configured_model = (
        cli_model
        or config.model
        or environment.get(MODEL_ENV)
    )
    if provider not in SUPPORTED_PROVIDERS:
        raise PurchaseProviderError(
            f"不支持的模型供应商：{provider}"
        )

    if provider == OPENAI_PROVIDER:
        api_key, credential_source = load_1688_openai_api_key(environment)
        if credential_override:
            api_key = credential_override.strip()
            credential_source = "interactive-input"
        if not api_key:
            raise PurchaseProviderError(
                "OpenAI API Key 尚未配置。请运行：as1688 provider"
            )
        return ProviderRuntime(
            provider=provider,
            model=configured_model or "",
            api_mode="openai_responses_sse",
            base_url="https://api.openai.com/v1",
            credential_source=credential_source,
            credential=api_key,
        )

    model = configured_model or DEFAULT_MODEL
    codex_path = shutil.which("codex")
    if codex_path is None:
        raise PurchaseProviderError(
            "未找到本机 codex 命令。请先安装 Codex CLI。"
        )
    try:
        login_status = subprocess.run(
            [codex_path, "login", "status"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PurchaseProviderError("检查 Codex 登录状态超时") from exc
    status_text = f"{login_status.stdout}\n{login_status.stderr}".strip().lower()
    if (
        login_status.returncode != 0
        or not status_text
        or "not logged" in status_text
    ):
        raise PurchaseProviderError(
            "本机 Codex 尚未登录。请先运行：codex login"
        )
    if "chatgpt" not in status_text:
        raise PurchaseProviderError(
            "当前 Codex 不是 ChatGPT 登录。请运行 codex logout，"
            "再运行 codex login 并选择 ChatGPT 登录。"
        )

    api_mode = (
        "codex_app_server"
        if config.openai_runtime == CODEX_RUNTIME_APP_SERVER
        else "codex_responses"
    )
    if api_mode == "codex_app_server":
        _verify_1688_codex_version(codex_path)

    return ProviderRuntime(
        provider=provider,
        model=model,
        api_mode=api_mode,
        base_url="https://chatgpt.com/backend-api/codex",
        credential_source="codex-cli-chatgpt-oauth",
        codex_path=codex_path,
    )


class CodexAppServerTransport:
    """一个很小的 Codex app-server JSON-RPC/JSONL 客户端。"""

    _END = object()

    def __init__(
        self,
        codex_path: str,
        timeout_seconds: int,
        *,
        server_request_handler: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.codex_path = codex_path
        self.timeout_seconds = timeout_seconds
        self._process: subprocess.Popen[str] | None = None
        self._messages: queue.Queue[dict[str, Any] | object] = queue.Queue()
        self._pending: deque[dict[str, Any]] = deque()
        self._stderr_tail: deque[str] = deque(maxlen=20)
        self._request_id = 0
        self._write_lock = threading.Lock()
        self._server_request_handler = server_request_handler

    def start_1688_codex_connection(self) -> None:
        if self._process is not None:
            return
        child_environment = _build_1688_codex_subprocess_environment()
        command = [
            self.codex_path,
            "app-server",
        ]
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=child_environment,
            )
        except OSError as exc:
            raise PurchaseProviderError("无法启动 Codex app-server") from exc

        threading.Thread(
            target=self._read_stdout,
            name="1688-codex-jsonl-reader",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._read_stderr,
            name="1688-codex-error-reader",
            daemon=True,
        ).start()

        self.request_1688_codex(
            "initialize",
            {
                "clientInfo": {
                    "name": "agent_search_1688",
                    "title": "1688 Agent Search",
                    "version": "0.2.0",
                }
            },
        )
        self.notify_1688_codex("initialized", {})

    def close_1688_codex_connection(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def __enter__(self) -> "CodexAppServerTransport":
        self.start_1688_codex_connection()
        return self

    def __exit__(self, *_: object) -> None:
        self.close_1688_codex_connection()

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            self._messages.put(self._END)
            return
        try:
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict):
                    self._messages.put(message)
        finally:
            self._messages.put(self._END)

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            self._stderr_tail.append(line.rstrip())

    def _send(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise PurchaseProviderError("Codex app-server 未运行")
        serialized = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            try:
                process.stdin.write(serialized + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise PurchaseProviderError("Codex app-server 连接已断开") from exc

    def notify_1688_codex(self, method: str, params: dict[str, Any]) -> None:
        self._send({"method": method, "params": params})

    def request_1688_codex(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        self._send({"method": method, "id": request_id, "params": params})
        deadline = time.monotonic() + (timeout_seconds or self.timeout_seconds)

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PurchaseProviderError(f"等待 {method} 响应超时")
            # RPC 等待期间只读新的队列消息。若从 _pending 读取再放回，
            # 同一条通知会被反复弹出，造成无限循环。
            message = self._receive_1688_codex_message(
                timeout_seconds=remaining
            )
            if "method" in message and "id" in message:
                self.handle_1688_codex_server_request(message)
                continue
            if message.get("id") != request_id:
                self._pending.append(message)
                continue
            if "error" in message:
                error = message.get("error") or {}
                detail = (
                    error.get("message", "未知错误")
                    if isinstance(error, dict)
                    else "未知错误"
                )
                raise PurchaseProviderError(f"Codex {method} 失败：{detail}")
            result = message.get("result")
            return result if isinstance(result, dict) else {}

    def next_1688_codex_message(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        if self._pending:
            return self._pending.popleft()
        return self._receive_1688_codex_message(
            timeout_seconds=timeout_seconds
        )

    def _receive_1688_codex_message(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        wait_seconds = (
            self.timeout_seconds
            if timeout_seconds is None
            else max(0.01, timeout_seconds)
        )
        try:
            message = self._messages.get(timeout=wait_seconds)
        except queue.Empty as exc:
            raise PurchaseProviderError("等待 Codex 流式回复超时") from exc
        if message is self._END:
            code = self._process.poll() if self._process is not None else None
            raise PurchaseProviderError(
                f"Codex app-server 已退出（状态码：{code}）"
            )
        assert isinstance(message, dict)
        return message

    def respond_1688_codex(
        self,
        request_id: Any,
        result: dict[str, Any],
    ) -> None:
        self._send({"id": request_id, "result": result})

    def respond_1688_codex_error(
        self,
        request_id: Any,
        code: int,
        message: str,
    ) -> None:
        self._send(
            {
                "id": request_id,
                "error": {
                    "code": code,
                    "message": message,
                },
            }
        )

    def handle_1688_codex_server_request(
        self,
        message: dict[str, Any],
    ) -> None:
        if self._server_request_handler is not None:
            self._server_request_handler(message)
            return
        self.respond_1688_codex_error(
            message.get("id"),
            -32601,
            f"不支持的 Codex Server 请求：{message.get('method', '')}",
        )


def build_1688_codex_turn_request(
    *,
    thread_id: str,
    user_input: str,
) -> dict[str, Any]:
    """与 Hermes 一样，只把当前用户输入交给 app-server。"""

    return {
        "threadId": thread_id,
        "input": [{"type": "text", "text": user_input}],
    }


def build_1688_codex_history_items(
    history: list[Message],
) -> list[dict[str, Any]]:
    """把恢复历史保留为 Responses API 的真实 User/Assistant 角色。"""

    items: list[dict[str, Any]] = []
    for message in history:
        if message.role.value == "user":
            items.append(
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": message.content}
                    ],
                }
            )
        elif message.role.value == "assistant":
            items.append(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": message.content}
                    ],
                }
            )
        else:
            raise PurchaseInvalidResponse(
                f"普通对话不能恢复角色：{message.role.value}"
            )
    return items


class CodexStreamCollector:
    """按顺序拼接 Codex 增量，并只在 turn/completed 后确认完成。"""

    def __init__(self, thread_id: str, turn_id: str, model: str):
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.model = model
        self.parts: list[str] = []
        self.final_text: str | None = None
        self.usage = TokenUsage()
        self.completed = False
        self.turn_status: str | None = None
        self.error: str | None = None

    def consume_1688_codex_event(self, message: dict[str, Any]) -> str | None:
        method = message.get("method")
        params = message.get("params")
        if not isinstance(params, dict):
            return None

        event_thread_id = params.get("threadId")
        event_turn_id = params.get("turnId")
        if event_thread_id not in (None, self.thread_id):
            return None
        if event_turn_id not in (None, self.turn_id):
            turn = params.get("turn")
            if not isinstance(turn, dict) or turn.get("id") != self.turn_id:
                return None

        if method == "item/agentMessage/delta":
            delta = params.get("delta")
            if isinstance(delta, str):
                self.parts.append(delta)
                return delta

        if method in {"item/started", "item/completed"}:
            item = params.get("item")
            if isinstance(item, dict):
                item_type = item.get("type")
                if (
                    method == "item/completed"
                    and item_type == "agentMessage"
                    and isinstance(item.get("text"), str)
                ):
                    self.final_text = item["text"]

        if method == "thread/tokenUsage/updated":
            token_usage = params.get("tokenUsage")
            if isinstance(token_usage, dict):
                last = token_usage.get("last")
                if isinstance(last, dict):
                    self.usage = TokenUsage.from_codex(last)

        if method == "model/rerouted":
            destination = (
                params.get("toModel")
                or params.get("model")
                or params.get("targetModel")
            )
            if isinstance(destination, str):
                self.model = destination

        if method == "error":
            error = params.get("error")
            if isinstance(error, dict):
                self.error = str(error.get("message", "Codex 返回错误"))
            else:
                self.error = str(params.get("message", "Codex 返回错误"))

        if method == "turn/completed":
            turn = params.get("turn")
            if isinstance(turn, dict) and turn.get("id") == self.turn_id:
                self.turn_status = str(turn.get("status", ""))
                turn_error = turn.get("error")
                if isinstance(turn_error, dict):
                    self.error = str(turn_error.get("message", self.error or "请求失败"))
                self.completed = True
        return None

    def complete_1688_codex_stream(self) -> tuple[str, TokenUsage, str]:
        if not self.completed:
            raise PurchaseInvalidResponse("缺少 turn/completed 事件")
        if self.turn_status == "interrupted":
            raise PurchaseProviderInterrupted("用户已中止模型请求")
        if self.turn_status != "completed":
            raise PurchaseProviderError(self.error or "模型请求未成功完成")

        streamed = "".join(self.parts)
        content = self.final_text if self.final_text is not None else streamed
        if not content.strip():
            raise PurchaseInvalidResponse("模型已完成，但没有返回有效文字")
        return content, self.usage, self.model


class CodexPurchaseProviderAdapter:
    def __init__(
        self,
        provider_runtime: ProviderRuntime,
        config: PurchaseConfig,
        prompt_builder: PurchasePromptBuilder,
        *,
        cwd: Path,
    ):
        if not provider_runtime.codex_path:
            raise PurchaseProviderError("Codex Provider 缺少 codex 命令路径")
        self.provider_runtime = provider_runtime
        self.config = config
        self.prompt_builder = prompt_builder
        self.cwd = cwd
        self.transport = CodexAppServerTransport(
            provider_runtime.codex_path,
            config.request_timeout_seconds,
            server_request_handler=self._handle_1688_codex_server_request,
        )
        self.thread_id: str | None = None
        self.actual_model = provider_runtime.model
        self.active_turn_id: str | None = None
        self._pending_history: list[Message] = []

    def open_1688_purchase_session(
        self,
        session: PurchaseSession,
        history: list[Message],
    ) -> str:
        """只绑定本地 Session；与 Hermes 一样到首个 Turn 才启动 Codex。"""

        self._pending_history = list(history)
        self.thread_id = None
        return f"codex_pending_{session.id}"

    def _ensure_1688_app_server_thread(self) -> str:
        if self.thread_id is not None:
            return self.thread_id
        self.transport.start_1688_codex_connection()
        result = self.transport.request_1688_codex(
            "thread/start",
            {"cwd": str(self.cwd)},
        )

        thread = result.get("thread")
        thread_id = (
            thread.get("id") or thread.get("sessionId")
            if isinstance(thread, dict)
            else None
        )
        thread_id = thread_id or result.get("sessionId") or result.get("threadId")
        if not isinstance(thread_id, str) or not thread_id:
            raise PurchaseInvalidResponse("Codex 未返回有效 thread id")
        actual_model = result.get("model")
        if isinstance(actual_model, str) and actual_model:
            self.actual_model = actual_model
        if self._pending_history:
            history_items = build_1688_codex_history_items(
                self._pending_history
            )
            self.transport.request_1688_codex(
                "thread/inject_items",
                {
                    "threadId": thread_id,
                    "items": history_items,
                },
            )
        self._pending_history = []
        self.thread_id = thread_id
        return thread_id

    def switch_1688_purchase_model(self, model: str) -> None:
        self.provider_runtime = ProviderRuntime(
            provider=self.provider_runtime.provider,
            model=model,
            api_mode=self.provider_runtime.api_mode,
            base_url=self.provider_runtime.base_url,
            credential_source=self.provider_runtime.credential_source,
            codex_path=self.provider_runtime.codex_path,
        )
        self.actual_model = model

    def stream_1688_model_reply(
        self,
        *,
        user_input: str,
        user_message_id: str,
        on_stream_started: Callable[[], None],
        on_delta: Callable[[str], None],
    ) -> ProviderStreamResult:
        self._ensure_1688_app_server_thread()
        assert self.thread_id is not None
        request = build_1688_codex_turn_request(
            thread_id=self.thread_id,
            user_input=user_input,
        )
        del user_message_id

        try:
            result = self.transport.request_1688_codex(
                "turn/start",
                request,
            )
            turn = result.get("turn")
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            if not isinstance(turn_id, str) or not turn_id:
                raise PurchaseInvalidResponse("Codex 未返回有效 turn id")
            self.active_turn_id = turn_id
            collector = CodexStreamCollector(
                self.thread_id,
                turn_id,
                self.actual_model,
            )
            on_stream_started()

            while not collector.completed:
                message = self.transport.next_1688_codex_message()
                if "method" in message and "id" in message:
                    self.transport.handle_1688_codex_server_request(message)
                    continue
                delta = collector.consume_1688_codex_event(message)
                if delta:
                    on_delta(delta)

            content, usage, actual_model = collector.complete_1688_codex_stream()
            streamed_content = "".join(collector.parts)
            if not streamed_content and content:
                on_delta(content)
            self.actual_model = actual_model
            return ProviderStreamResult(
                content=content,
                usage=usage,
                actual_model=actual_model,
                provider_thread_id=self.thread_id,
            )
        except KeyboardInterrupt as exc:
            self.interrupt_1688_model_reply()
            raise PurchaseProviderInterrupted("用户已中止模型请求") from exc
        except PurchaseInvalidResponse:
            self.interrupt_1688_model_reply()
            raise
        finally:
            self.active_turn_id = None

    def interrupt_1688_model_reply(self) -> None:
        if self.thread_id is None or self.active_turn_id is None:
            return
        try:
            self.transport.request_1688_codex(
                "turn/interrupt",
                {
                    "threadId": self.thread_id,
                    "turnId": self.active_turn_id,
                },
                timeout_seconds=15,
            )
        except PurchaseProviderError:
            pass

    def close(self) -> None:
        self.transport.close_1688_codex_connection()

    def _handle_1688_codex_server_request(
        self,
        request: dict[str, Any],
    ) -> None:
        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params")
        params = params if isinstance(params, dict) else {}
        if method == "item/commandExecution/requestApproval":
            command = str(params.get("command") or "")
            reason = str(params.get("reason") or "Codex 请求执行命令")
            decision = self._prompt_1688_codex_approval(command, reason)
            self.transport.respond_1688_codex(
                request_id,
                {"decision": decision},
            )
            return
        if method == "item/fileChange/requestApproval":
            reason = str(params.get("reason") or "Codex 请求修改文件")
            decision = self._prompt_1688_codex_approval("apply_patch", reason)
            self.transport.respond_1688_codex(
                request_id,
                {"decision": decision},
            )
            return
        if method == "item/permissions/requestApproval":
            self.transport.respond_1688_codex(
                request_id,
                {"decision": "decline"},
            )
            return
        if method == "mcpServer/elicitation/request":
            accepted = params.get("serverName") == "1688-tools"
            self.transport.respond_1688_codex(
                request_id,
                {
                    "action": "accept" if accepted else "decline",
                    "content": None,
                    "_meta": None,
                },
            )
            return
        self.transport.respond_1688_codex_error(
            request_id,
            -32601,
            f"不支持的 Codex Server 请求：{method}",
        )

    @staticmethod
    def _prompt_1688_codex_approval(command: str, reason: str) -> str:
        print(f"\nCodex 请求授权：{reason}")
        if command:
            print(command)
        try:
            answer = input("允许本次操作？[y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return "decline"
        return "accept" if answer in {"y", "yes", "是"} else "decline"


def list_1688_codex_models(
    provider_runtime: ProviderRuntime,
    *,
    timeout_seconds: int = 60,
) -> list[ModelOption]:
    """直接读取 OAuth Codex 模型目录；不会启动 app-server。"""

    from .codex_auth import (
        build_codex_chatgpt_headers,
        load_local_codex_chatgpt_auth,
        refresh_local_codex_auth,
    )

    endpoint = (
        "https://chatgpt.com/backend-api/codex/models?"
        + urllib.parse.urlencode({"client_version": "1.0.0"})
    )
    payload: dict[str, Any] | None = None
    for attempt in range(2):
        token = load_local_codex_chatgpt_auth()["access_token"]
        request = urllib.request.Request(
            endpoint,
            headers=build_codex_chatgpt_headers(token),
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                candidate = json.loads(response.read(2_000_001).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and attempt == 0:
                refresh_local_codex_auth()
                continue
            raise PurchaseProviderError(
                f"读取 Codex 模型目录失败（HTTP {exc.code}）"
            ) from exc
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            raise PurchaseProviderError(f"读取 Codex 模型目录失败：{exc}") from exc
        if isinstance(candidate, dict):
            payload = candidate
            break
    entries = payload.get("models") if payload is not None else None
    if not isinstance(entries, list):
        raise PurchaseInvalidResponse("Codex 模型目录格式无效")
    sortable: list[tuple[int, ModelOption]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        model = item.get("slug")
        if not isinstance(model, str) or not model.strip():
            continue
        visibility = item.get("visibility")
        if isinstance(visibility, str) and visibility.lower() in {"hide", "hidden"}:
            continue
        priority = item.get("priority")
        rank = int(priority) if isinstance(priority, (int, float)) else 10_000
        sortable.append(
            (
                rank,
                ModelOption(
                    model=model.strip(),
                    display_name=str(
                        item.get("display_name")
                        or item.get("displayName")
                        or model
                    ),
                    description=str(item.get("description") or ""),
                    is_default=model.strip() == provider_runtime.model,
                ),
            )
        )
    sortable.sort(key=lambda entry: (entry[0], entry[1].model))
    models = [option for _, option in sortable]
    if not models:
        raise PurchaseInvalidResponse("Codex 模型目录没有返回可用模型")
    return models


def list_1688_provider_models(
    provider_runtime: ProviderRuntime,
    *,
    timeout_seconds: int = 60,
) -> list[ModelOption]:
    """通过统一入口读取当前供应商的模型目录。"""

    if provider_runtime.provider == CODEX_PROVIDER:
        return list_1688_codex_models(
            provider_runtime,
            timeout_seconds=timeout_seconds,
        )
    if provider_runtime.provider == OPENAI_PROVIDER:
        from .openai import list_1688_openai_models

        return list_1688_openai_models(
            provider_runtime,
            timeout_seconds=timeout_seconds,
        )
    raise PurchaseProviderError(
        f"不支持的模型供应商：{provider_runtime.provider}"
    )
