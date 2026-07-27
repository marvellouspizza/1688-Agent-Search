"""本机 Codex ChatGPT OAuth 凭据与请求头。"""

from __future__ import annotations

import base64
import fcntl
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

from .codex import PurchaseProviderError


CODEX_AUTH_PATH = Path.home() / ".codex" / "auth.json"
CODEX_AUTH_LOCK_PATH = Path.home() / ".codex" / "auth.json.as1688.lock"
CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"


def load_local_codex_chatgpt_auth() -> dict[str, str]:
    """读取 Codex CLI 的 OAuth 存储，但绝不记录令牌值。"""

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


def build_codex_chatgpt_headers(access_token: str) -> dict[str, str]:
    """构造与 Hermes openai-codex 相同的 Codex 后端请求头。"""

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "codex_cli_rs/0.0.0 (1688 Agent Search)",
        "originator": "codex_cli_rs",
    }
    try:
        payload = access_token.split(".")[1]
        claims = json.loads(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        )
        account_id = claims.get("https://api.openai.com/auth", {}).get(
            "chatgpt_account_id"
        )
        if isinstance(account_id, str) and account_id:
            headers["ChatGPT-Account-ID"] = account_id
    except (IndexError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        pass
    return headers


def refresh_local_codex_auth() -> dict[str, str]:
    """在进程锁下刷新并原子更新 Codex CLI OAuth 存储。"""

    CODEX_AUTH_LOCK_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with CODEX_AUTH_LOCK_PATH.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        credentials = load_local_codex_chatgpt_auth()
        encoded = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": credentials["refresh_token"],
                "client_id": CODEX_OAUTH_CLIENT_ID,
            }
        ).encode()
        request = urllib.request.Request(
            CODEX_OAUTH_TOKEN_URL,
            data=encoded,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                refreshed = json.loads(response.read(100_001).decode("utf-8"))
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            raise PurchaseProviderError("Codex 登录已失效，请运行：codex login") from exc
        access = refreshed.get("access_token") if isinstance(refreshed, dict) else None
        refresh = refreshed.get("refresh_token") if isinstance(refreshed, dict) else None
        if not isinstance(access, str) or not access:
            raise PurchaseProviderError("Codex 登录刷新未返回 access token")

        payload = json.loads(CODEX_AUTH_PATH.read_text(encoding="utf-8"))
        tokens = payload.get("tokens") if isinstance(payload, dict) else None
        if not isinstance(tokens, dict):
            raise PurchaseProviderError("Codex 登录文件在刷新期间变为无效")
        tokens["access_token"] = access
        tokens["refresh_token"] = (
            refresh
            if isinstance(refresh, str) and refresh
            else credentials["refresh_token"]
        )
        payload["last_refresh"] = int(time.time())
        temporary = CODEX_AUTH_PATH.with_suffix(".as1688.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(CODEX_AUTH_PATH)
        return {
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
        }
