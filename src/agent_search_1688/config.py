"""1688 Agent Search 的非秘密配置。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
from typing import Any


APP_HOME_ENV = "AGENT_SEARCH_1688_HOME"
MODEL_ENV = "AGENT_SEARCH_1688_MODEL"
PROVIDER_ENV = "AGENT_SEARCH_1688_PROVIDER"
CODEX_PROVIDER = "local-codex-chatgpt"
OPENAI_PROVIDER = "openai-api"
DEFAULT_PROVIDER = CODEX_PROVIDER
DEFAULT_MODEL = "gpt-5.6-sol"
SUPPORTED_PROVIDERS = (CODEX_PROVIDER, OPENAI_PROVIDER)


class PurchaseConfigError(RuntimeError):
    pass


def get_1688_purchase_home() -> Path:
    overridden = os.environ.get(APP_HOME_ENV)
    if overridden:
        return Path(overridden).expanduser().resolve()
    return Path.home() / ".1688-agent-search"


@dataclass(frozen=True)
class PurchaseConfig:
    provider: str | None = None
    model: str | None = None
    database_path: str | None = None
    request_timeout_seconds: int = 300
    max_context_characters: int = 120_000
    searxng_base_url: str = "http://127.0.0.1:8888"
    searxng_timeout_seconds: int = 30
    # Matches Hermes' default per-turn tool-calling iteration budget.
    max_iterations: int = 500

    @property
    def resolved_database_path(self) -> Path:
        raw_path = self.database_path
        if raw_path:
            return Path(raw_path).expanduser().resolve()
        return get_1688_purchase_home() / "sessions.db"


def get_1688_purchase_config_path() -> Path:
    return get_1688_purchase_home() / "config.json"


def _validate_1688_purchase_config(data: dict[str, Any]) -> PurchaseConfig:
    allowed = {
        "provider",
        "model",
        "database_path",
        "request_timeout_seconds",
        "max_context_characters",
        "searxng_base_url",
        "searxng_timeout_seconds",
        "max_iterations",
    }
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise PurchaseConfigError(f"配置包含未知字段：{', '.join(unknown)}")

    for field_name in (
        "provider",
        "model",
        "database_path",
        "searxng_base_url",
    ):
        value = data.get(field_name)
        if value is not None and not isinstance(value, str):
            raise PurchaseConfigError(f"{field_name} 必须是字符串")
    for field_name in (
        "request_timeout_seconds",
        "max_context_characters",
        "searxng_timeout_seconds",
        "max_iterations",
    ):
        if field_name in data and type(data[field_name]) is not int:
            raise PurchaseConfigError(f"{field_name} 必须是整数")

    config = PurchaseConfig(**data)
    if config.provider is not None and not config.provider.strip():
        raise PurchaseConfigError("provider 不能为空")
    if config.model is not None and not config.model.strip():
        raise PurchaseConfigError("model 不能为空")
    if config.request_timeout_seconds <= 0:
        raise PurchaseConfigError("request_timeout_seconds 必须大于 0")
    if config.max_context_characters <= 0:
        raise PurchaseConfigError("max_context_characters 必须大于 0")
    if config.searxng_timeout_seconds <= 0:
        raise PurchaseConfigError("searxng_timeout_seconds 必须大于 0")
    if not config.searxng_base_url.strip():
        raise PurchaseConfigError("searxng_base_url 不能为空")
    if config.max_iterations <= 0:
        raise PurchaseConfigError("max_iterations 必须大于 0")
    return config


def load_1688_purchase_config() -> PurchaseConfig:
    """读取普通配置；任何供应商凭证都不会从这里读取。"""

    path = get_1688_purchase_config_path()
    if not path.exists():
        return PurchaseConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PurchaseConfigError(f"配置文件损坏：{path}") from exc
    if not isinstance(raw, dict):
        raise PurchaseConfigError("配置文件顶层必须是 JSON 对象")
    return _validate_1688_purchase_config(raw)


def save_1688_purchase_config(config: PurchaseConfig) -> Path:
    """仅保存 Provider、模型等非秘密配置。"""

    path = get_1688_purchase_config_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {
        key: value
        for key, value in asdict(config).items()
        if value is not None
    }
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.chmod(0o600)
    temporary_path.replace(path)
    return path


def with_1688_purchase_model(config: PurchaseConfig, model: str) -> PurchaseConfig:
    return replace(
        config,
        provider=config.provider or DEFAULT_PROVIDER,
        model=model,
    )


def with_1688_purchase_provider(
    config: PurchaseConfig,
    provider: str,
    model: str | None = None,
) -> PurchaseConfig:
    if provider not in SUPPORTED_PROVIDERS:
        raise PurchaseConfigError(f"不支持的供应商：{provider}")
    return replace(config, provider=provider, model=model)
