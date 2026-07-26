"""供应商凭证存取；秘密永远不进入普通 config.json。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess

from .config import get_1688_purchase_home


OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
KEYCHAIN_SERVICE = "as1688.openai.api-key"
KEYCHAIN_ACCOUNT = "openai"


class PurchaseCredentialError(RuntimeError):
    pass


def _credential_file_path() -> Path:
    return get_1688_purchase_home() / "credentials.json"


def _is_1688_keychain_item_not_found(
    result: subprocess.CompletedProcess[str],
) -> bool:
    error_text = result.stderr.lower()
    return result.returncode == 44 or "could not be found" in error_text


def _delete_1688_credential_file_if_present() -> bool:
    path = _credential_file_path()
    if not (path.exists() or path.is_symlink()):
        return False
    try:
        path.unlink()
    except OSError as exc:
        raise PurchaseCredentialError(f"无法删除凭证文件：{path}") from exc
    return True


def _load_1688_keychain_api_key() -> str | None:
    if platform.system() != "Darwin":
        return None
    security_path = shutil.which("security")
    if security_path is None:
        return None
    try:
        result = subprocess.run(
            [
                security_path,
                "find-generic-password",
                "-a",
                KEYCHAIN_ACCOUNT,
                "-s",
                KEYCHAIN_SERVICE,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _load_1688_credential_file_api_key() -> str | None:
    path = _credential_file_path()
    if not path.exists():
        return None
    try:
        if path.is_symlink():
            raise PurchaseCredentialError("凭证文件不能是符号链接")
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise PurchaseCredentialError(
                f"凭证文件权限过宽，请执行：chmod 600 {path}"
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PurchaseCredentialError(f"凭证文件损坏：{path}") from exc
    if not isinstance(payload, dict):
        raise PurchaseCredentialError("凭证文件格式无效")
    value = payload.get("openai_api_key")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PurchaseCredentialError("OpenAI API Key 格式无效")
    return value.strip()


def load_1688_openai_api_key(
    environ: dict[str, str] | None = None,
) -> tuple[str | None, str]:
    environment = environ if environ is not None else os.environ
    environment_key = environment.get(OPENAI_API_KEY_ENV, "").strip()
    if environment_key:
        return environment_key, f"environment:{OPENAI_API_KEY_ENV}"

    keychain_key = _load_1688_keychain_api_key()
    if keychain_key:
        return keychain_key, "macos-keychain"

    file_key = _load_1688_credential_file_api_key()
    if file_key:
        return file_key, "credential-file"
    return None, "not-configured"


def save_1688_openai_api_key(api_key: str) -> str:
    value = api_key.strip()
    if not value or any(character.isspace() for character in value):
        raise PurchaseCredentialError("OpenAI API Key 不能为空或包含空白字符")

    if platform.system() == "Darwin":
        security_path = shutil.which("security")
        if security_path is not None:
            try:
                result = subprocess.run(
                    [
                        security_path,
                        "add-generic-password",
                        "-U",
                        "-a",
                        KEYCHAIN_ACCOUNT,
                        "-s",
                        KEYCHAIN_SERVICE,
                        "-w",
                    ],
                    input=value + "\n",
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise PurchaseCredentialError(
                    "无法访问 macOS 钥匙串，OpenAI API Key 没有修改"
                ) from exc
            if result.returncode != 0:
                raise PurchaseCredentialError(
                    "macOS 钥匙串拒绝保存，OpenAI API Key 没有修改"
                )
            if _load_1688_keychain_api_key() != value:
                raise PurchaseCredentialError(
                    "macOS 钥匙串保存后校验失败"
                )
            _delete_1688_credential_file_if_present()
            return "macos-keychain"

    path = _credential_file_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps({"openai_api_key": value}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary_path.chmod(0o600)
    temporary_path.replace(path)
    path.chmod(0o600)
    if _load_1688_credential_file_api_key() != value:
        raise PurchaseCredentialError("凭证文件保存后校验失败")
    return "credential-file"


def delete_1688_openai_api_key() -> list[str]:
    """删除 as1688 自己保存的 OpenAI Key，不修改环境变量。"""

    removed_sources: list[str] = []
    if platform.system() == "Darwin":
        security_path = shutil.which("security")
        if security_path is not None:
            try:
                result = subprocess.run(
                    [
                        security_path,
                        "delete-generic-password",
                        "-a",
                        KEYCHAIN_ACCOUNT,
                        "-s",
                        KEYCHAIN_SERVICE,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise PurchaseCredentialError("无法访问 macOS 钥匙串") from exc
            if result.returncode == 0:
                removed_sources.append("macos-keychain")
            elif not _is_1688_keychain_item_not_found(result):
                raise PurchaseCredentialError(
                    "macOS 钥匙串拒绝删除 OpenAI API Key"
                )

    if _delete_1688_credential_file_if_present():
        removed_sources.append("credential-file")
    return removed_sources
