import {
  chmodSync,
  closeSync,
  existsSync,
  lstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";

import { PurchaseProviderError } from "./errors.js";

export const CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token";
export const CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann";

export interface CodexAuth {
  readonly accessToken: string;
  readonly refreshToken: string;
}

export interface CodexAuthOptions {
  readonly codexHome?: string;
  readonly environ?: Readonly<Record<string, string | undefined>>;
  readonly fetchImpl?: typeof fetch;
}

export function getLocalCodexHome(options: CodexAuthOptions = {}): string {
  if (options.codexHome) return resolve(options.codexHome);
  const configured = (options.environ ?? process.env).CODEX_HOME?.trim();
  return configured ? resolve(expandUser(configured)) : join(homedir(), ".codex");
}

export function loadLocalCodexChatGptAuth(options: CodexAuthOptions = {}): CodexAuth {
  const { payload } = readAuthPayload(options);
  if (payload.auth_mode !== "chatgpt") {
    throw new PurchaseProviderError("当前 Codex 不是 ChatGPT 登录。请运行：codex login");
  }
  const tokens = payload.tokens;
  if (!isRecord(tokens)) throw new PurchaseProviderError("本机 Codex 登录缺少 OAuth 凭据");
  const accessToken = tokens.access_token;
  const refreshToken = tokens.refresh_token;
  if (typeof accessToken !== "string" || !accessToken || typeof refreshToken !== "string" || !refreshToken) {
    throw new PurchaseProviderError("本机 Codex 登录缺少可用 OAuth 凭据");
  }
  return { accessToken, refreshToken };
}

export function buildCodexChatGptHeaders(accessToken: string): Record<string, string> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
    Accept: "application/json",
    "User-Agent": "codex_cli_rs/0.0.0 (1688 Agent Search)",
    originator: "codex_cli_rs",
  };
  try {
    const segment = accessToken.split(".")[1];
    if (!segment) return headers;
    const claims: unknown = JSON.parse(Buffer.from(segment, "base64url").toString("utf8"));
    if (isRecord(claims)) {
      const auth = claims["https://api.openai.com/auth"];
      if (isRecord(auth) && typeof auth.chatgpt_account_id === "string" && auth.chatgpt_account_id) {
        headers["ChatGPT-Account-ID"] = auth.chatgpt_account_id;
      }
    }
  } catch {
    // Account metadata is optional; the bearer token remains authoritative.
  }
  return headers;
}

export async function refreshLocalCodexAuth(options: CodexAuthOptions = {}): Promise<CodexAuth> {
  const codexHome = getLocalCodexHome(options);
  mkdirSync(codexHome, { recursive: true, mode: 0o700 });
  const release = await acquireRefreshLock(join(codexHome, "auth.json.as1688.lock"));
  try {
    const current = loadLocalCodexChatGptAuth(options);
    let response: Response;
    try {
      response = await (options.fetchImpl ?? fetch)(CODEX_OAUTH_TOKEN_URL, {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "refresh_token",
          refresh_token: current.refreshToken,
          client_id: CODEX_OAUTH_CLIENT_ID,
        }),
        signal: AbortSignal.timeout(20_000),
      });
    } catch (error) {
      throw new PurchaseProviderError("Codex 登录已失效，请运行：codex login", { cause: error });
    }
    if (!response.ok) throw new PurchaseProviderError("Codex 登录已失效，请运行：codex login");
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.byteLength > 100_000) throw new PurchaseProviderError("Codex 登录刷新响应过大");
    let refreshed: unknown;
    try {
      refreshed = JSON.parse(new TextDecoder().decode(bytes));
    } catch (error) {
      throw new PurchaseProviderError("Codex 登录已失效，请运行：codex login", { cause: error });
    }
    if (!isRecord(refreshed) || typeof refreshed.access_token !== "string" || !refreshed.access_token) {
      throw new PurchaseProviderError("Codex 登录刷新未返回 access token");
    }
    const { path, payload } = readAuthPayload(options);
    if (!isRecord(payload.tokens)) throw new PurchaseProviderError("Codex 登录文件在刷新期间变为无效");
    const refreshToken = typeof refreshed.refresh_token === "string" && refreshed.refresh_token
      ? refreshed.refresh_token
      : current.refreshToken;
    payload.tokens.access_token = refreshed.access_token;
    payload.tokens.refresh_token = refreshToken;
    payload.last_refresh = new Date().toISOString();
    const temporary = join(dirname(path), ".auth.json.as1688.tmp");
    writeFileSync(temporary, `${JSON.stringify(payload)}\n`, { encoding: "utf8", mode: 0o600 });
    chmodSync(temporary, 0o600);
    renameSync(temporary, path);
    return { accessToken: refreshed.access_token, refreshToken };
  } finally {
    release();
  }
}

function readAuthPayload(options: CodexAuthOptions): { path: string; payload: Record<string, unknown> } {
  const path = join(getLocalCodexHome(options), "auth.json");
  let payload: unknown;
  try {
    const stat = lstatSync(path);
    if (stat.isSymbolicLink()) throw new PurchaseProviderError("Codex 登录文件不能是符号链接");
    if ((stat.mode & 0o077) !== 0) throw new PurchaseProviderError(`Codex 登录文件权限过宽，请执行：chmod 600 ${path}`);
    payload = JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    if (error instanceof PurchaseProviderError) throw error;
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      throw new PurchaseProviderError("未找到本机 Codex 登录。请先运行：codex login", { cause: error });
    }
    throw new PurchaseProviderError("本机 Codex 登录文件无效", { cause: error });
  }
  if (!isRecord(payload)) throw new PurchaseProviderError("本机 Codex 登录文件无效");
  return { path, payload };
}

async function acquireRefreshLock(path: string): Promise<() => void> {
  const deadline = Date.now() + 20_000;
  while (true) {
    try {
      const descriptor = openSync(path, "wx", 0o600);
      writeFileSync(descriptor, JSON.stringify({ pid: process.pid, startedAt: Date.now() }));
      return () => {
        try { closeSync(descriptor); } catch { /* already closed */ }
        rmSync(path, { force: true });
      };
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
      if (!lockIsAlive(path)) {
        rmSync(path, { force: true });
        continue;
      }
      if (Date.now() >= deadline) throw new PurchaseProviderError("等待 Codex 登录刷新锁超时");
      await new Promise<void>((resolvePromise) => setTimeout(resolvePromise, 50));
    }
  }
}

function lockIsAlive(path: string): boolean {
  if (!existsSync(path)) return false;
  try {
    const payload: unknown = JSON.parse(readFileSync(path, "utf8"));
    if (!isRecord(payload) || !Number.isSafeInteger(payload.pid)) return false;
    process.kill(Number(payload.pid), 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "EPERM";
  }
}

function expandUser(path: string): string {
  return path === "~" ? homedir() : path.startsWith("~/") ? join(homedir(), path.slice(2)) : path;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
