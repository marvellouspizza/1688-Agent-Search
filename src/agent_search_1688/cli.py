"""终端入口：只处理输入、展示和本地命令。"""

from __future__ import annotations

import argparse
from dataclasses import replace
import getpass
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Sequence

from .config import (
    CODEX_PROVIDER,
    MODEL_ENV,
    OPENAI_PROVIDER,
    PROVIDER_ENV,
    PurchaseConfigError,
    PurchaseConfig,
    SUPPORTED_PROVIDERS,
    get_1688_purchase_config_path,
    load_1688_purchase_config,
    save_1688_purchase_config,
    with_1688_purchase_model,
    with_1688_purchase_provider,
)
from .credentials import (
    OPENAI_API_KEY_ENV,
    PurchaseCredentialError,
    delete_1688_openai_api_key,
    load_1688_openai_api_key,
    save_1688_openai_api_key,
)
from .models import ChatStatus, ModelOption, ProviderRuntime
from .provider import (
    PurchaseProviderError,
    list_1688_provider_models,
    resolve_1688_purchase_provider,
)
from .runtime import PurchaseAgentRuntime, create_1688_purchase_agent
from .session_store import PurchaseSessionStore


def build_1688_purchase_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="as1688",
        description="1688 智能采购项目的 GPT 对话 CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    chat = subparsers.add_parser("chat", help="启动交互对话")
    chat.add_argument("-q", "--question", help="单次提问后退出")
    chat.add_argument("-s", "--session", help="创建或恢复指定 Session")
    chat.add_argument("-m", "--model", help="仅本次启动覆盖默认模型")

    model = subparsers.add_parser(
        "model",
        help="绑定、查看或选择当前供应商的模型",
    )
    model.add_argument("--list", action="store_true", help="列出可用模型")
    model.add_argument("--set", metavar="MODEL", help="保存默认模型")
    model.add_argument("--login", action="store_true", help="执行 codex login")
    model.add_argument("--status", action="store_true", help="查看绑定状态")

    provider = subparsers.add_parser(
        "provider",
        help="选择或查看模型供应商",
    )
    provider_action = provider.add_mutually_exclusive_group()
    provider_action.add_argument(
        "--list", action="store_true", help="列出供应商"
    )
    provider_action.add_argument(
        "--set", metavar="PROVIDER", help="选择供应商"
    )
    provider_action.add_argument(
        "--status", action="store_true", help="查看供应商"
    )
    provider_action.add_argument(
        "--update-key",
        action="store_true",
        help="验证并更新保存的 OpenAI API Key",
    )
    provider_action.add_argument(
        "--delete-key",
        action="store_true",
        help="删除 as1688 保存的 OpenAI API Key",
    )

    sessions = subparsers.add_parser("sessions", help="列出最近会话")
    sessions.add_argument("--limit", type=int, default=20, help="最多显示数量")
    subparsers.add_parser("mcp-server", help=argparse.SUPPRESS)
    return parser


def _format_1688_welcome_screen(
    provider_name: str | None,
    model_name: str | None,
    session_name: str | None,
) -> str:
    """生成固定宽度的欢迎界面，长名称会安全截断。"""

    width = 50

    def line(content: str = "") -> str:
        shortened = content
        if len(shortened) > width:
            shortened = shortened[: width - 3] + "..."
        return f"|{shortened.ljust(width)}|"

    provider_label = provider_name or "Not configured"
    model_label = model_name or "Not configured"
    session_label = session_name or "Waiting for model"
    return "\n".join(
        [
            "+" + "-" * width + "+",
            line(),
            line("       /\\_/\\        AGENT SEARCH 1688"),
            line("      ( o.o )       Smart Sourcing Assistant"),
            line("       > ^ <"),
            line(),
            line(f"  Provider: {provider_label}"),
            line(f"  Model   : {model_label}"),
            line(f"  Session : {session_label}"),
            line("  Help    : /help"),
            line(),
            "+" + "-" * width + "+",
        ]
    )


def _print_1688_welcome_screen(
    provider_name: str | None,
    model_name: str | None,
    session_name: str | None,
) -> None:
    print(
        _format_1688_welcome_screen(
            provider_name,
            model_name,
            session_name,
        )
    )


PROVIDER_DISPLAY_NAMES = {
    CODEX_PROVIDER: "Local Codex / ChatGPT",
    OPENAI_PROVIDER: "OpenAI API",
}


def _display_1688_provider_name(provider: str) -> str:
    return PROVIDER_DISPLAY_NAMES.get(provider, provider)


def _print_1688_provider_options(current_provider: str | None) -> None:
    print("可用供应商：")
    descriptions = {
        CODEX_PROVIDER: "复用本机 Codex 的 ChatGPT 登录",
        OPENAI_PROVIDER: "使用你自己的 OpenAI API Key",
    }
    for index, provider in enumerate(SUPPORTED_PROVIDERS, start=1):
        selected = " ← 当前" if provider == current_provider else ""
        print(
            f"  {index}. {_display_1688_provider_name(provider)} "
            f"[{provider}]{selected}"
        )
        print(f"     {descriptions[provider]}")


def _choose_1688_provider(current_provider: str | None) -> str | None:
    _print_1688_provider_options(current_provider)
    try:
        answer = input("输入序号选择供应商（直接回车取消）：").strip()
    except EOFError:
        return None
    if not answer:
        return None
    try:
        index = int(answer)
    except ValueError:
        print("请输入列表中的数字。")
        return None
    if not 1 <= index <= len(SUPPORTED_PROVIDERS):
        print("序号不在列表中。")
        return None
    return SUPPORTED_PROVIDERS[index - 1]


def _display_1688_model_name(
    models: list[ModelOption],
    model: str,
) -> str:
    return next(
        (
            option.display_name
            for option in models
            if option.model == model
        ),
        model,
    )


def _print_1688_model_options(
    models: list[ModelOption],
    current_model: str | None,
) -> None:
    if not models:
        print("没有读取到可用模型。")
        return
    print("可用模型：")
    for index, option in enumerate(models, start=1):
        selected = " ← 当前" if option.model == current_model else ""
        default = "（Codex 默认）" if option.is_default else ""
        print(
            f"  {index}. {option.display_name}  "
            f"[{option.model}]{default}{selected}"
        )
        if option.description:
            print(f"     {option.description}")


def _choose_1688_model(
    models: list[ModelOption],
    current_model: str | None,
) -> str | None:
    _print_1688_model_options(models, current_model)
    if not models:
        return None
    try:
        answer = input("输入序号选择模型（直接回车取消）：").strip()
    except EOFError:
        return None
    if not answer:
        return None
    try:
        index = int(answer)
    except ValueError:
        print("请输入列表中的数字。")
        return None
    if not 1 <= index <= len(models):
        print("序号不在列表中。")
        return None
    return models[index - 1].model


def _prompt_1688_openai_api_key() -> str | None:
    try:
        value = getpass.getpass("请输入 OpenAI API Key（输入不会显示）：").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    return value or None


def _resolve_1688_provider_and_models(
    config: PurchaseConfig,
    provider: str,
    *,
    cli_model: str | None = None,
) -> tuple[ProviderRuntime, list[ModelOption]] | None:
    credential_override: str | None = None
    should_save_credential = False
    if provider == OPENAI_PROVIDER:
        api_key, _source = load_1688_openai_api_key()
        if not api_key:
            api_key = _prompt_1688_openai_api_key()
            if api_key is None:
                print("尚未输入 OpenAI API Key。")
                return None
            credential_override = api_key
            should_save_credential = True

    provider_runtime = resolve_1688_purchase_provider(
        config,
        cli_model=cli_model,
        cli_provider=provider,
        credential_override=credential_override,
    )
    models = list_1688_provider_models(
        provider_runtime,
        timeout_seconds=config.request_timeout_seconds,
    )
    if should_save_credential:
        assert credential_override is not None
        source = save_1688_openai_api_key(credential_override)
        provider_runtime = replace(
            provider_runtime,
            credential_source=source,
        )
        print(f"OpenAI API Key 已保存到安全凭证存储：{source}")
    return provider_runtime, models


def _configure_1688_provider_and_model(
    config: PurchaseConfig,
    requested_provider: str | None = None,
) -> tuple[PurchaseConfig, ProviderRuntime, list[ModelOption]] | None:
    provider = requested_provider or _choose_1688_provider(config.provider)
    if provider is None:
        return None
    if provider not in SUPPORTED_PROVIDERS:
        raise PurchaseConfigError(f"不支持的供应商：{provider}")

    provider_config = with_1688_purchase_provider(config, provider)
    resolved = _resolve_1688_provider_and_models(provider_config, provider)
    if resolved is None:
        return None
    provider_runtime, models = resolved
    current_model = config.model if config.provider == provider else None
    selected_model = _choose_1688_model(models, current_model)
    if selected_model is None:
        print("尚未选择模型，供应商配置没有修改。")
        return None

    configured = with_1688_purchase_provider(
        config,
        provider,
        selected_model,
    )
    save_1688_purchase_config(configured)
    provider_runtime = replace(provider_runtime, model=selected_model)
    print(f"供应商已保存：{_display_1688_provider_name(provider)}")
    print(f"默认模型已保存：{selected_model}")
    return configured, provider_runtime, models


def _update_1688_openai_api_key(config: PurchaseConfig) -> int:
    if os.environ.get(OPENAI_API_KEY_ENV, "").strip():
        print(
            f"当前正在使用环境变量 {OPENAI_API_KEY_ENV}；它的优先级最高。\n"
            f"请先执行 unset {OPENAI_API_KEY_ENV}，再运行此命令。",
            file=sys.stderr,
        )
        return 1

    api_key = _prompt_1688_openai_api_key()
    if api_key is None:
        print("API Key 没有修改。")
        return 0

    openai_config = with_1688_purchase_provider(config, OPENAI_PROVIDER)
    provider_runtime = resolve_1688_purchase_provider(
        openai_config,
        cli_provider=OPENAI_PROVIDER,
        credential_override=api_key,
    )
    models = list_1688_provider_models(
        provider_runtime,
        timeout_seconds=config.request_timeout_seconds,
    )
    source = save_1688_openai_api_key(api_key)
    print(f"OpenAI API Key 验证成功，可用文本模型：{len(models)} 个")
    print(f"OpenAI API Key 已更新：{source}")
    return 0


def _delete_1688_saved_openai_api_key() -> int:
    removed_sources = delete_1688_openai_api_key()
    if removed_sources:
        print("已删除 OpenAI API Key：" + "、".join(removed_sources))
    else:
        print("没有找到由 as1688 保存的 OpenAI API Key。")
    if os.environ.get(OPENAI_API_KEY_ENV, "").strip():
        print(
            f"注意：环境变量 {OPENAI_API_KEY_ENV} 仍然有效；"
            f"如需停用，请执行 unset {OPENAI_API_KEY_ENV}。"
        )
    return 0


def run_1688_provider_command(args: argparse.Namespace) -> int:
    try:
        config = load_1688_purchase_config()
        if args.update_key:
            return _update_1688_openai_api_key(config)
        if args.delete_key:
            return _delete_1688_saved_openai_api_key()
        configured_provider = config.provider or os.environ.get(PROVIDER_ENV)
        if args.list:
            _print_1688_provider_options(configured_provider)
            return 0
        if args.status:
            print(
                "当前供应商："
                + (
                    _display_1688_provider_name(configured_provider)
                    if configured_provider
                    else "尚未配置"
                )
            )
            print(f"供应商 ID：{configured_provider or '尚未配置'}")
            print(f"默认模型：{config.model or '尚未配置'}")
            return 0
        result = _configure_1688_provider_and_model(config, args.set)
        return 0 if result is not None else 0
    except (
        PurchaseConfigError,
        PurchaseCredentialError,
        PurchaseProviderError,
        OSError,
    ) as exc:
        print(f"供应商配置失败：{exc}", file=sys.stderr)
        return 1


def run_1688_model_command(args: argparse.Namespace) -> int:
    if args.login:
        codex_path = shutil.which("codex")
        if codex_path is None:
            print("错误：未找到 codex 命令。", file=sys.stderr)
            return 1
        return subprocess.run([codex_path, "login"], check=False).returncode

    try:
        config = load_1688_purchase_config()
        provider = config.provider or os.environ.get(PROVIDER_ENV)
        if provider is None:
            print("尚未选择供应商，请先选择供应商。")
            result = _configure_1688_provider_and_model(config)
            return 0 if result is not None else 0
        resolved = _resolve_1688_provider_and_models(config, provider)
        if resolved is None:
            return 0
        provider_runtime, models = resolved
    except (
        PurchaseConfigError,
        PurchaseCredentialError,
        PurchaseProviderError,
    ) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    if args.status:
        print("绑定状态：已配置")
        print(f"凭证来源：{provider_runtime.credential_source}")
        print(
            f"Provider：{_display_1688_provider_name(provider_runtime.provider)}"
        )
        configured_model = config.model or os.environ.get(MODEL_ENV)
        print(f"默认模型：{configured_model or '尚未配置'}")
        print(f"配置文件：{get_1688_purchase_config_path()}")
        return 0

    if args.list:
        _print_1688_model_options(
            models,
            config.model or os.environ.get(MODEL_ENV),
        )
        return 0

    selected_model = args.set
    if selected_model:
        known_models = {option.model for option in models}
        if selected_model not in known_models:
            print(
                f"错误：模型不在当前供应商可用目录中：{selected_model}",
                file=sys.stderr,
            )
            _print_1688_model_options(models, provider_runtime.model)
            return 1
    else:
        selected_model = _choose_1688_model(
            models,
            config.model or os.environ.get(MODEL_ENV),
        )

    if selected_model is None:
        return 0
    path = save_1688_purchase_config(
        with_1688_purchase_model(config, selected_model)
    )
    print(f"默认模型已保存：{selected_model}")
    print(f"配置文件：{path}")
    return 0


def _show_1688_chat_help() -> None:
    print(
        "\n会话内命令：\n"
        "  /model               选择本 Session 使用的模型\n"
        "  /model MODEL         直接切换模型\n"
        "  /session             显示当前 Session ID\n"
        "  /stop                空闲时无动作；生成中请按 Ctrl+C\n"
        "  /help                显示帮助\n"
        "  /quit                退出\n"
    )


def _switch_1688_chat_model(
    agent: PurchaseAgentRuntime,
    requested_model: str | None,
) -> None:
    models = list_1688_provider_models(
        agent.provider_runtime,
        timeout_seconds=agent.config.request_timeout_seconds,
    )
    selected = requested_model
    known_models = {option.model for option in models}
    if selected is None:
        selected = _choose_1688_model(models, agent.provider_runtime.model)
    elif selected not in known_models:
        print(f"模型不可用：{selected}")
        _print_1688_model_options(models, agent.provider_runtime.model)
        return
    if selected:
        agent.switch_1688_purchase_model(selected)
        print(f"本 Session 已切换到：{selected}")


def _route_1688_chat_command(
    text: str,
    agent: PurchaseAgentRuntime,
) -> bool:
    command, _, argument = text.partition(" ")
    argument = argument.strip()
    if command == "/quit":
        return False
    if command == "/help":
        _show_1688_chat_help()
    elif command == "/session":
        assert agent.session is not None
        print(f"当前 Session：{agent.session.id}")
    elif command == "/stop":
        print("当前没有正在生成的回复；生成过程中可按 Ctrl+C 中止。")
    elif command == "/model":
        try:
            _switch_1688_chat_model(agent, argument or None)
        except PurchaseProviderError as exc:
            print(f"切换模型失败：{exc}")
    else:
        print(f"未知命令：{command}。输入 /help 查看命令。")
    return True


def _ask_1688_purchase_agent(
    agent: PurchaseAgentRuntime,
    text: str,
) -> ChatStatus:
    print("1688 Agent > ", end="", flush=True)
    result = agent.chat(
        text,
        on_delta=lambda delta: print(delta, end="", flush=True),
    )
    print()
    if result.status is not ChatStatus.COMPLETED:
        print(f"[{result.status.value}] {result.error or '请求未完成'}")
    return result.status


def run_1688_chat_command(args: argparse.Namespace) -> int:
    agent: PurchaseAgentRuntime | None = None
    welcome_already_shown = False
    try:
        config = load_1688_purchase_config()
        configured_provider = config.provider or os.environ.get(PROVIDER_ENV)
        configured_model = (
            args.model or config.model or os.environ.get(MODEL_ENV)
        )
        if configured_provider is None or configured_model is None:
            if args.question is None:
                _print_1688_welcome_screen(
                    (
                        _display_1688_provider_name(configured_provider)
                        if configured_provider
                        else None
                    ),
                    configured_model,
                    None,
                )
                welcome_already_shown = True
                print("\n首次使用，请先选择供应商和默认模型。")
            setup = _configure_1688_provider_and_model(
                config,
                configured_provider,
            )
            if setup is None:
                print("配置尚未完成。以后可运行：as1688 provider")
                return 0
            config, provider_runtime, models = setup
        else:
            resolved = _resolve_1688_provider_and_models(
                config,
                configured_provider,
                cli_model=args.model,
            )
            if resolved is None:
                return 0
            provider_runtime, models = resolved

        known_models = {option.model for option in models}
        if provider_runtime.model not in known_models:
            raise PurchaseProviderError(
                f"模型不在当前供应商可用目录中：{provider_runtime.model}。"
                "请运行：as1688 model"
            )
        session_store = PurchaseSessionStore(config.resolved_database_path)
        agent = create_1688_purchase_agent(
            config=config,
            provider_runtime=provider_runtime,
            session_store=session_store,
            cwd=Path.cwd(),
        )
        session = agent.create_or_restore_1688_purchase_session(args.session)
    except (
        PurchaseConfigError,
        PurchaseCredentialError,
        PurchaseProviderError,
        OSError,
        ValueError,
    ) as exc:
        if agent is not None:
            agent.close()
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1

    try:
        if args.question is not None:
            if not args.question.strip():
                print("问题不能为空。", file=sys.stderr)
                return 1
            status = _ask_1688_purchase_agent(agent, args.question)
            return 0 if status is ChatStatus.COMPLETED else 1

        display_model = _display_1688_model_name(
            models,
            agent.provider_runtime.model,
        )
        display_session = session.id if args.session else "New conversation"
        display_provider = _display_1688_provider_name(
            agent.provider_runtime.provider
        )
        if welcome_already_shown:
            print(f"供应商已就绪：{display_provider}")
            print(f"模型已就绪：{display_model}")
            print(f"Session：{display_session}")
            print("输入 /help 查看命令，输入 /quit 退出。")
        else:
            _print_1688_welcome_screen(
                display_provider,
                display_model,
                display_session,
            )

        while True:
            try:
                text = input("\n你 > ")
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print("\n已退出。")
                break
            if not text.strip():
                continue
            if text.startswith("/"):
                if not _route_1688_chat_command(text.strip(), agent):
                    break
                continue
            _ask_1688_purchase_agent(agent, text)
        return 0
    finally:
        agent.close()


def run_1688_sessions_command(args: argparse.Namespace) -> int:
    store: PurchaseSessionStore | None = None
    try:
        config = load_1688_purchase_config()
        store = PurchaseSessionStore(config.resolved_database_path)
        sessions = store.list_1688_purchase_sessions(max(1, args.limit))
    except (PurchaseConfigError, OSError) as exc:
        print(f"读取 Session 失败：{exc}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()
    if not sessions:
        print("还没有保存过 Session。")
        return 0
    for session in sessions:
        print(
            f"{session.id}  {session.provider}  {session.model}  "
            f"更新时间：{session.updated_at}"
        )
    return 0


def run_1688_purchase_cli(argv: Sequence[str] | None = None) -> int:
    parser = build_1688_purchase_cli_parser()
    raw_arguments = list(argv) if argv is not None else sys.argv[1:]
    if not raw_arguments:
        raw_arguments = ["chat"]
    args = parser.parse_args(raw_arguments)
    if args.command == "chat":
        return run_1688_chat_command(args)
    if args.command == "model":
        return run_1688_model_command(args)
    if args.command == "provider":
        return run_1688_provider_command(args)
    if args.command == "sessions":
        return run_1688_sessions_command(args)
    if args.command == "mcp-server":
        from .mcp_server import run_1688_mcp_server

        return run_1688_mcp_server()
    parser.error(f"未知命令：{args.command}")
    return 2


def main() -> None:
    raise SystemExit(run_1688_purchase_cli())
