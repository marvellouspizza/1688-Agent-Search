import {
  chmodSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { delimiter, dirname, join } from "node:path";
import { spawnSync } from "node:child_process";

import { getPurchaseHome } from "./config.js";

export const OPENAI_API_KEY_ENV = "OPENAI_API_KEY";
export const KEYCHAIN_SERVICE = "as1688.openai.api-key";
export const KEYCHAIN_ACCOUNT = "openai";

export class PurchaseCredentialError extends Error {
  override readonly name = "PurchaseCredentialError";
}

export interface CredentialOptions {
  readonly appHome?: string;
  readonly environ?: Readonly<Record<string, string | undefined>>;
  readonly platform?: NodeJS.Platform;
  readonly securityPath?: string;
}

export interface LoadedCredential {
  readonly apiKey?: string;
  readonly source: string;
}

export function loadOpenAiApiKey(options: CredentialOptions = {}): LoadedCredential {
  const environ = options.environ ?? process.env;
  const environmentKey = environ[OPENAI_API_KEY_ENV]?.trim();
  if (environmentKey) {
    return { apiKey: environmentKey, source: `environment:${OPENAI_API_KEY_ENV}` };
  }
  const keychainKey = loadKeychainApiKey(options);
  if (keychainKey) return { apiKey: keychainKey, source: "macos-keychain" };
  const fileKey = loadCredentialFileApiKey(options);
  if (fileKey) return { apiKey: fileKey, source: "credential-file" };
  return { source: "not-configured" };
}

export function saveOpenAiApiKey(apiKey: string, options: CredentialOptions = {}): string {
  const value = apiKey.trim();
  if (!value || /\s/u.test(value)) {
    throw new PurchaseCredentialError("OpenAI API Key 不能为空或包含空白字符");
  }
  const securityPath = resolveSecurityPath(options);
  if ((options.platform ?? process.platform) === "darwin" && securityPath) {
    const result = spawnSync(securityPath, [
      "add-generic-password", "-U", "-a", KEYCHAIN_ACCOUNT, "-s", KEYCHAIN_SERVICE, "-w",
    ], {
      input: `${value}\n`,
      encoding: "utf8",
      timeout: 15_000,
      stdio: ["pipe", "pipe", "pipe"],
    });
    if (result.error) {
      throw new PurchaseCredentialError("无法访问 macOS 钥匙串，OpenAI API Key 没有修改", { cause: result.error });
    }
    if (result.status !== 0) {
      throw new PurchaseCredentialError("macOS 钥匙串拒绝保存，OpenAI API Key 没有修改");
    }
    if (loadKeychainApiKey({ ...options, securityPath }) !== value) {
      throw new PurchaseCredentialError("macOS 钥匙串保存后校验失败");
    }
    deleteCredentialFileIfPresent(options);
    return "macos-keychain";
  }

  const path = credentialFilePath(options);
  mkdirSync(options.appHome ?? getPurchaseHome(pathOptions(options)), { recursive: true, mode: 0o700 });
  const temporaryPath = join(dirname(path), ".credentials.tmp");
  writeFileSync(temporaryPath, `${JSON.stringify({ openai_api_key: value })}\n`, { encoding: "utf8", mode: 0o600 });
  chmodSync(temporaryPath, 0o600);
  renameSync(temporaryPath, path);
  chmodSync(path, 0o600);
  if (loadCredentialFileApiKey(options) !== value) {
    throw new PurchaseCredentialError("凭证文件保存后校验失败");
  }
  return "credential-file";
}

export function deleteOpenAiApiKey(options: CredentialOptions = {}): string[] {
  const removed: string[] = [];
  const securityPath = resolveSecurityPath(options);
  if ((options.platform ?? process.platform) === "darwin" && securityPath) {
    const result = spawnSync(securityPath, [
      "delete-generic-password", "-a", KEYCHAIN_ACCOUNT, "-s", KEYCHAIN_SERVICE,
    ], { encoding: "utf8", timeout: 15_000, stdio: ["ignore", "pipe", "pipe"] });
    if (result.error) throw new PurchaseCredentialError("无法访问 macOS 钥匙串", { cause: result.error });
    if (result.status === 0) {
      removed.push("macos-keychain");
    } else if (!(result.status === 44 || result.stderr.toLowerCase().includes("could not be found"))) {
      throw new PurchaseCredentialError("macOS 钥匙串拒绝删除 OpenAI API Key");
    }
  }
  if (deleteCredentialFileIfPresent(options)) removed.push("credential-file");
  return removed;
}

function credentialFilePath(options: CredentialOptions): string {
  return join(options.appHome ?? getPurchaseHome(pathOptions(options)), "credentials.json");
}

function loadCredentialFileApiKey(options: CredentialOptions): string | undefined {
  const path = credentialFilePath(options);
  if (!existsSync(path)) return undefined;
  let payload: unknown;
  try {
    const stat = lstatSync(path);
    if (stat.isSymbolicLink()) throw new PurchaseCredentialError("凭证文件不能是符号链接");
    if ((stat.mode & 0o077) !== 0) {
      throw new PurchaseCredentialError(`凭证文件权限过宽，请执行：chmod 600 ${path}`);
    }
    payload = JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    if (error instanceof PurchaseCredentialError) throw error;
    throw new PurchaseCredentialError(`凭证文件损坏：${path}`, { cause: error });
  }
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    throw new PurchaseCredentialError("凭证文件格式无效");
  }
  const value = (payload as Record<string, unknown>).openai_api_key;
  if (value === undefined) return undefined;
  if (typeof value !== "string" || !value.trim()) {
    throw new PurchaseCredentialError("OpenAI API Key 格式无效");
  }
  return value.trim();
}

function loadKeychainApiKey(options: CredentialOptions): string | undefined {
  if ((options.platform ?? process.platform) !== "darwin") return undefined;
  const securityPath = resolveSecurityPath(options);
  if (!securityPath) return undefined;
  const result = spawnSync(securityPath, [
    "find-generic-password", "-a", KEYCHAIN_ACCOUNT, "-s", KEYCHAIN_SERVICE, "-w",
  ], { encoding: "utf8", timeout: 15_000, stdio: ["ignore", "pipe", "pipe"] });
  if (result.error || result.status !== 0) return undefined;
  return result.stdout.trim() || undefined;
}

function deleteCredentialFileIfPresent(options: CredentialOptions): boolean {
  const path = credentialFilePath(options);
  if (!existsSync(path) && !safeIsSymlink(path)) return false;
  try {
    rmSync(path);
    return true;
  } catch (error) {
    throw new PurchaseCredentialError(`无法删除凭证文件：${path}`, { cause: error });
  }
}

function safeIsSymlink(path: string): boolean {
  try {
    return lstatSync(path).isSymbolicLink();
  } catch {
    return false;
  }
}

function resolveSecurityPath(options: CredentialOptions): string | undefined {
  if (options.securityPath !== undefined) return options.securityPath || undefined;
  if ((options.platform ?? process.platform) !== "darwin") return undefined;
  if (existsSync("/usr/bin/security")) return "/usr/bin/security";
  const pathValue = (options.environ ?? process.env).PATH ?? "";
  for (const directory of pathValue.split(delimiter)) {
    const candidate = join(directory, "security");
    if (existsSync(candidate)) return candidate;
  }
  return undefined;
}

function pathOptions(options: CredentialOptions): { environ?: Readonly<Record<string, string | undefined>> } {
  return options.environ === undefined ? {} : { environ: options.environ };
}
