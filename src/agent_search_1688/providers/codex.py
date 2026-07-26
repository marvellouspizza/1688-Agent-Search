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
import sys
import threading
import time
from typing import Any, Callable

from ..config import (
    CODEX_PROVIDER,
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


MINIMUM_CODEX_VERSION = (0, 144, 0)
MAXIMUM_CODEX_VERSION_EXCLUSIVE = (0, 145, 0)

# Codex 0.144.x 中会向模型暴露非文字能力的 Feature。这个集合既用于
# 启动 app-server，也用于每个 Thread。升级到 0.145+ 时必须重新审计。
CODEX_0144_NON_TEXT_FEATURES = {
    "apps",
    "auth_elicitation",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "code_mode",
    "code_mode_host",
    "code_mode_only",
    "computer_use",
    "default_mode_request_user_input",
    "enable_fanout",
    "enable_mcp_apps",
    "exec_permission_approvals",
    "goals",
    "guardian_approval",
    "hooks",
    "image_generation",
    "in_app_browser",
    "memories",
    "mentions_v2",
    "multi_agent",
    "multi_agent_v2",
    "network_proxy",
    "plugin_sharing",
    "plugins",
    "remote_plugin",
    "request_permissions_tool",
    "shell_snapshot",
    "shell_tool",
    "skill_mcp_dependency_install",
    "standalone_web_search",
    "tool_call_mcp_elicitation",
    "tool_search_always_defer_mcp_tools",
    "tool_suggest",
    "unified_exec",
    "workspace_dependencies",
}

CODEX_0144_SAFE_ACTIVE_FEATURES = {
    "collaboration_modes",
    "enable_request_compression",
    "fast_mode",
    "personality",
    "remote_compaction_v2",
    "resize_all_images",
    "sqlite",
    "steer",
    "terminal_resize_reflow",
    "tui_app_server",
}


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
    if installed >= MAXIMUM_CODEX_VERSION_EXCLUSIVE:
        current = ".".join(str(part) for part in installed)
        raise PurchaseProviderError(
            f"Codex CLI {current} 尚未完成普通对话能力审计。"
            "请先更新 1688 Agent Search。"
        )


def _verify_1688_codex_feature_catalog(codex_path: str) -> None:
    try:
        feature_result = subprocess.run(
            [codex_path, "features", "list"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PurchaseProviderError("无法确认 Codex 工具能力已关闭") from exc
    if feature_result.returncode != 0:
        raise PurchaseProviderError("无法确认 Codex 工具能力已关闭")

    active_features: set[str] = set()
    catalog_entries = 0
    for line in feature_result.stdout.splitlines():
        match = re.match(r"^(\S+)\s+.+\s+(true|false)\s*$", line)
        if match:
            catalog_entries += 1
            if match.group(2) == "true":
                active_features.add(match.group(1))
    if catalog_entries == 0:
        raise PurchaseProviderError("Codex 未返回可验证的能力目录")
    classified = (
        CODEX_0144_NON_TEXT_FEATURES | CODEX_0144_SAFE_ACTIVE_FEATURES
    )
    unknown_active = sorted(active_features - classified)
    if unknown_active:
        raise PurchaseProviderError(
            "发现尚未审计的 Codex 能力，已拒绝启动："
            + ", ".join(unknown_active)
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

    return ProviderRuntime(
        provider=provider,
        model=model,
        api_mode="codex_responses",
        base_url="https://chatgpt.com/backend-api/codex",
        credential_source="codex-cli-chatgpt-oauth",
        codex_path=codex_path,
    )


class CodexAppServerTransport:
    """一个很小的 Codex app-server JSON-RPC/JSONL 客户端。"""

    _END = object()

    def __init__(self, codex_path: str, timeout_seconds: int):
        self.codex_path = codex_path
        self.timeout_seconds = timeout_seconds
        self._process: subprocess.Popen[str] | None = None
        self._messages: queue.Queue[dict[str, Any] | object] = queue.Queue()
        self._pending: deque[dict[str, Any]] = deque()
        self._stderr_tail: deque[str] = deque(maxlen=20)
        self._request_id = 0
        self._write_lock = threading.Lock()

    def start_1688_codex_connection(self) -> None:
        if self._process is not None:
            return
        child_environment = os.environ.copy()
        child_environment["RUST_LOG"] = "error"
        command = [
            self.codex_path,
            "app-server",
            "--stdio",
            "-c",
            'web_search="disabled"',
            "-c",
            "tools.view_image=false",
        ]
        for feature in sorted(CODEX_0144_NON_TEXT_FEATURES):
            command.extend(["--disable", feature])
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
                self._reject_server_request(message)
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

    def _reject_server_request(self, message: dict[str, Any]) -> None:
        self._send(
            {
                "id": message["id"],
                "error": {
                    "code": -32601,
                    "message": "1688 Agent Search 普通对话阶段不允许工具或审批请求",
                },
            }
        )


def build_1688_codex_turn_request(
    *,
    thread_id: str,
    user_input: str,
    model: str,
    user_message_id: str,
) -> dict[str, Any]:
    """把统一内部输入转换成当前 Codex app-server 请求格式。"""

    return {
        "threadId": thread_id,
        "input": [{"type": "text", "text": user_input}],
        "model": model,
        "clientUserMessageId": user_message_id,
        "approvalPolicy": "on-request",
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


def _build_1688_disabled_mcp_entry(
    entry: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    name = entry.get("name")
    transport = entry.get("transport")
    if not isinstance(name, str) or not name:
        raise PurchaseProviderError("Codex MCP 清单包含无效名称")
    if not isinstance(transport, dict):
        raise PurchaseProviderError(f"Codex MCP {name} 缺少传输定义")

    transport_type = transport.get("type")
    disabled: dict[str, Any] = {"enabled": False}
    if transport_type == "stdio":
        command = transport.get("command")
        if not isinstance(command, str) or not command:
            raise PurchaseProviderError(f"Codex MCP {name} 缺少启动命令")
        disabled["command"] = command
        optional_fields: dict[str, type] = {
            "args": list,
            "env": dict,
            "env_vars": list,
            "cwd": str,
        }
    elif transport_type == "streamable_http":
        url = transport.get("url")
        if not isinstance(url, str) or not url:
            raise PurchaseProviderError(f"Codex MCP {name} 缺少 URL")
        disabled["url"] = url
        optional_fields = {
            "bearer_token_env_var": str,
            "http_headers": dict,
            "env_http_headers": dict,
        }
    else:
        raise PurchaseProviderError(
            f"Codex MCP {name} 使用了尚未审计的传输类型"
        )

    for field, expected_type in optional_fields.items():
        value = transport.get(field)
        if value is None:
            continue
        if not isinstance(value, expected_type):
            raise PurchaseProviderError(
                f"Codex MCP {name} 的 {field} 格式无效"
            )
        disabled[field] = value

    for timeout_field in ("startup_timeout_sec", "tool_timeout_sec"):
        value = entry.get(timeout_field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PurchaseProviderError(
                f"Codex MCP {name} 的 {timeout_field} 格式无效"
            )
        disabled[timeout_field] = value
    return name, disabled


def _discover_1688_codex_mcp_config(
    codex_path: str,
    cwd: Path,
) -> dict[str, dict[str, Any]]:
    """让 Codex 解析完整 TOML，并构造保留传输信息的禁用配置。"""

    try:
        result = subprocess.run(
            [codex_path, "mcp", "list", "--json"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PurchaseProviderError("无法确认 Codex MCP 已关闭") from exc
    if result.returncode != 0:
        raise PurchaseProviderError("无法确认 Codex MCP 已关闭")
    try:
        entries = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PurchaseProviderError("Codex 未返回可验证的 MCP 清单") from exc
    if not isinstance(entries, list):
        raise PurchaseProviderError("Codex 未返回可验证的 MCP 清单")

    disabled_config: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise PurchaseProviderError("Codex MCP 清单格式无效")
        name, disabled = _build_1688_disabled_mcp_entry(entry)
        if name in disabled_config:
            raise PurchaseProviderError(f"Codex MCP 名称重复：{name}")
        disabled_config[name] = disabled
    return disabled_config


def _build_1688_tools_mcp_config() -> dict[str, Any]:
    """仅向 Codex 暴露本项目自己的只读工具 Registry。"""

    launcher = Path(sys.argv[0]).resolve()
    environment = {
        "AGENT_SEARCH_1688_HOME": os.environ.get("AGENT_SEARCH_1688_HOME", ""),
    }
    if launcher.suffix == ".pyz":
        args = [str(launcher), "mcp-server"]
    else:
        source_root = Path(__file__).resolve().parents[1]
        existing_pythonpath = os.environ.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = (
            str(source_root)
            if not existing_pythonpath
            else f"{source_root}{os.pathsep}{existing_pythonpath}"
        )
        args = ["-m", "agent_search_1688", "mcp-server"]
    return {
        "enabled": True,
        "command": sys.executable,
        "args": args,
        "env": environment,
        "startup_timeout_sec": 15,
        "tool_timeout_sec": 45,
    }


class CodexStreamCollector:
    """按顺序拼接 Codex 增量，并只在 turn/completed 后确认完成。"""

    allowed_item_types = {
        "userMessage",
        "agentMessage",
        "reasoning",
        "mcpToolCall",
    }

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
                    isinstance(item_type, str)
                    and item_type not in self.allowed_item_types
                ):
                    raise PurchaseInvalidResponse(
                        f"普通对话收到不允许的工具项：{item_type}"
                    )
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
        self._mcp_config = _discover_1688_codex_mcp_config(
            provider_runtime.codex_path,
            cwd,
        )
        self._mcp_config["1688-tools"] = _build_1688_tools_mcp_config()
        self.transport = CodexAppServerTransport(
            provider_runtime.codex_path,
            config.request_timeout_seconds,
        )
        self.thread_id: str | None = None
        self.actual_model = provider_runtime.model
        self.active_turn_id: str | None = None
        self._tool_free_config: dict[str, Any] | None = None

    def _build_1688_tool_free_config(self) -> dict[str, Any]:
        """禁用外部能力，仅允许本项目受控的 1688-tools MCP。"""

        if self._tool_free_config is not None:
            return self._tool_free_config

        skills_result = self.transport.request_1688_codex(
            "skills/list",
            {
                "cwds": [str(self.cwd)],
                "forceReload": False,
            },
        )
        skill_config: list[dict[str, Any]] = []
        skill_entries = skills_result.get("data")
        if not isinstance(skill_entries, list):
            raise PurchaseProviderError("无法确认 Codex Skill 已关闭")
        for entry in skill_entries:
            if not isinstance(entry, dict):
                continue
            skills = entry.get("skills")
            if not isinstance(skills, list):
                continue
            for skill in skills:
                if isinstance(skill, dict) and isinstance(skill.get("path"), str):
                    skill_config.append(
                        {"path": skill["path"], "enabled": False}
                    )

        plugins_result = self.transport.request_1688_codex(
            "plugin/list",
            {
                "cwds": [str(self.cwd)],
                "marketplaceKinds": ["local"],
            },
        )
        marketplaces = plugins_result.get("marketplaces")
        if not isinstance(marketplaces, list):
            raise PurchaseProviderError("无法确认 Codex Plugin 已关闭")
        plugin_config: dict[str, dict[str, bool]] = {}
        for marketplace in marketplaces:
            if not isinstance(marketplace, dict):
                continue
            plugins = marketplace.get("plugins")
            if not isinstance(plugins, list):
                continue
            for plugin in plugins:
                if isinstance(plugin, dict) and isinstance(plugin.get("id"), str):
                    plugin_config[plugin["id"]] = {"enabled": False}

        self._tool_free_config = {
            "web_search": "disabled",
            "features": {
                feature: False
                for feature in CODEX_0144_NON_TEXT_FEATURES
            },
            "tools": {"view_image": False},
            "plugins": plugin_config,
            "mcp_servers": self._mcp_config,
            "skills": {"config": skill_config},
        }
        return self._tool_free_config

    def open_1688_purchase_session(
        self,
        session: PurchaseSession,
        history: list[Message],
    ) -> str:
        self.transport.start_1688_codex_connection()
        base_instructions = (
            self.prompt_builder.build_1688_purchase_base_instructions()
        )
        normal_context = self.prompt_builder.build_1688_purchase_context(
            session_id=session.id,
            provider_runtime=self.provider_runtime,
        )
        tool_free_config = self._build_1688_tool_free_config()

        result: dict[str, Any] | None = None
        started_new_thread = False
        if session.provider_thread_id:
            try:
                result = self.transport.request_1688_codex(
                    "thread/resume",
                    {
                        "threadId": session.provider_thread_id,
                        "model": self.provider_runtime.model,
                        "cwd": str(self.cwd),
                        "approvalPolicy": "on-request",
                        "sandbox": "read-only",
                        "baseInstructions": base_instructions,
                        "developerInstructions": normal_context,
                        "config": tool_free_config,
                    },
                )
            except PurchaseProviderError:
                result = None

        if result is None:
            started_new_thread = True
            result = self.transport.request_1688_codex(
                "thread/start",
                {
                    "model": self.provider_runtime.model,
                    "cwd": str(self.cwd),
                    "approvalPolicy": "on-request",
                    "sandbox": "read-only",
                    "baseInstructions": base_instructions,
                    "developerInstructions": normal_context,
                    "ephemeral": False,
                    "config": tool_free_config,
                },
            )

        thread = result.get("thread")
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise PurchaseInvalidResponse("Codex 未返回有效 thread id")
        self.thread_id = thread_id
        actual_model = result.get("model")
        if isinstance(actual_model, str) and actual_model:
            self.actual_model = actual_model
        if started_new_thread and history:
            history_items = build_1688_codex_history_items(history)
            self.transport.request_1688_codex(
                "thread/inject_items",
                {
                    "threadId": thread_id,
                    "items": history_items,
                },
            )
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
        if self.thread_id is None:
            raise PurchaseProviderError("Provider Session 尚未创建")
        request = build_1688_codex_turn_request(
            thread_id=self.thread_id,
            user_input=user_input,
            model=self.provider_runtime.model,
            user_message_id=user_message_id,
        )

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
                    self.transport._reject_server_request(message)
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


def list_1688_codex_models(
    provider_runtime: ProviderRuntime,
    *,
    timeout_seconds: int = 60,
) -> list[ModelOption]:
    """从本机 Codex 模型目录读取用户当前真正可选的模型。"""

    if not provider_runtime.codex_path:
        raise PurchaseProviderError("Codex Provider 缺少 codex 命令路径")
    models: list[ModelOption] = []
    cursor: str | None = None
    with CodexAppServerTransport(
        provider_runtime.codex_path,
        timeout_seconds,
    ) as transport:
        while True:
            params: dict[str, Any] = {
                "includeHidden": False,
                "limit": 100,
            }
            if cursor:
                params["cursor"] = cursor
            result = transport.request_1688_codex("model/list", params)
            data = result.get("data")
            if isinstance(data, list):
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    model = item.get("model") or item.get("id")
                    if not isinstance(model, str) or not model:
                        continue
                    models.append(
                        ModelOption(
                            model=model,
                            display_name=str(item.get("displayName") or model),
                            description=str(item.get("description") or ""),
                            is_default=bool(item.get("isDefault", False)),
                            hidden=bool(item.get("hidden", False)),
                        )
                    )
            next_cursor = result.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            cursor = next_cursor
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
