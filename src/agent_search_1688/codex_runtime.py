"""Hermes-style optional Codex app-server runtime switch and MCP setup."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any

from .config import (
    CODEX_RUNTIME_APP_SERVER,
    CODEX_RUNTIME_AUTO,
    SKILL_ROOT_ENV,
    SUPPORTED_CODEX_RUNTIMES,
    resolve_1688_skill_root,
)


MANAGED_START = "# >>> 1688 Agent Search managed Codex runtime >>>"
MANAGED_END = "# <<< 1688 Agent Search managed Codex runtime <<<"


def parse_1688_codex_runtime(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "on": CODEX_RUNTIME_APP_SERVER,
        "codex": CODEX_RUNTIME_APP_SERVER,
        "enable": CODEX_RUNTIME_APP_SERVER,
        "off": CODEX_RUNTIME_AUTO,
        "default": CODEX_RUNTIME_AUTO,
        "disable": CODEX_RUNTIME_AUTO,
        "hermes": CODEX_RUNTIME_AUTO,
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in SUPPORTED_CODEX_RUNTIMES:
        raise ValueError(
            "Runtime 必须是 auto 或 codex_app_server（也可使用 on/off）"
        )
    return normalized


def build_1688_tools_mcp_config(*, cwd: Path | None = None) -> dict[str, Any]:
    """生成 Codex 可启动的项目 MCP 回调定义。"""

    launcher = Path(sys.argv[0]).resolve()
    environment: dict[str, str] = {
        SKILL_ROOT_ENV: str(resolve_1688_skill_root(cwd)),
    }
    configured_home = os.environ.get("AGENT_SEARCH_1688_HOME")
    if configured_home:
        environment["AGENT_SEARCH_1688_HOME"] = configured_home
    if launcher.suffix == ".pyz":
        args = [str(launcher), "mcp-server"]
    else:
        source_root = Path(__file__).resolve().parents[1]
        existing_pythonpath = os.environ.get("PYTHONPATH")
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


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(
            f"{json.dumps(str(key), ensure_ascii=False)} = {_toml_value(item)}"
            for key, item in value.items()
        ) + " }"
    raise TypeError(f"不能写入 Codex TOML 的值：{type(value).__name__}")


def _managed_1688_codex_block(*, cwd: Path | None = None) -> str:
    lines = [MANAGED_START, "", '[mcp_servers."1688-tools"]']
    for key, value in build_1688_tools_mcp_config(cwd=cwd).items():
        lines.append(f"{key} = {_toml_value(value)}")
    lines.extend(("", MANAGED_END, ""))
    return "\n".join(lines)


def _strip_managed_1688_codex_block(text: str) -> str:
    pattern = re.compile(
        rf"(?ms)^\s*{re.escape(MANAGED_START)}\n.*?"
        rf"^{re.escape(MANAGED_END)}\s*\n?"
    )
    if (MANAGED_START in text) != (MANAGED_END in text):
        raise ValueError("Codex 配置中的 as1688 托管区块不完整")
    return pattern.sub("", text).rstrip() + ("\n" if text.strip() else "")


def install_1688_codex_runtime_mcp(
    *,
    cwd: Path | None = None,
    codex_home: Path | None = None,
) -> Path:
    """像 Hermes 一样，把项目工具回调注册到 Codex app-server。"""

    resolved_home = (codex_home or Path.home() / ".codex").expanduser()
    target = resolved_home / "config.toml"
    try:
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        preserved = _strip_managed_1688_codex_block(existing)
        content = preserved
        if content and not content.endswith("\n"):
            content += "\n"
        if content:
            content += "\n"
        content += _managed_1688_codex_block(cwd=cwd)
        resolved_home.mkdir(mode=0o700, parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".config.toml.as1688.",
            dir=str(resolved_home),
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
            mode = target.stat().st_mode & 0o777 if target.exists() else 0o600
            temporary.chmod(mode)
            temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(f"无法注册 1688-tools MCP：{exc}") from exc
    return target
