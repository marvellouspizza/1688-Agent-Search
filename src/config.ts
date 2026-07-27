import {
  chmodSync,
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";

export const APP_HOME_ENV = "AGENT_SEARCH_1688_HOME";
export const SKILL_ROOT_ENV = "AGENT_SEARCH_1688_SKILL_ROOT";
export const MODEL_ENV = "AGENT_SEARCH_1688_MODEL";
export const PROVIDER_ENV = "AGENT_SEARCH_1688_PROVIDER";
export const CODEX_PROVIDER = "local-codex-chatgpt";
export const OPENAI_PROVIDER = "openai-api";
export const DEFAULT_PROVIDER = CODEX_PROVIDER;
export const DEFAULT_MODEL = "gpt-5.6-sol";
export const CODEX_RUNTIME_AUTO = "auto";
export const CODEX_RUNTIME_APP_SERVER = "codex_app_server";
export const SUPPORTED_PROVIDERS = Object.freeze([CODEX_PROVIDER, OPENAI_PROVIDER] as const);
export const SUPPORTED_CODEX_RUNTIMES = Object.freeze([
  CODEX_RUNTIME_AUTO,
  CODEX_RUNTIME_APP_SERVER,
] as const);

export type SupportedProvider = (typeof SUPPORTED_PROVIDERS)[number];
export type CodexRuntime = (typeof SUPPORTED_CODEX_RUNTIMES)[number];

export interface PurchaseConfig {
  readonly provider?: string;
  readonly model?: string;
  readonly openaiRuntime: CodexRuntime;
  readonly databasePath?: string;
  readonly requestTimeoutSeconds: number;
  readonly maxContextCharacters: number;
  readonly searxngBaseUrl: string;
  readonly searxngTimeoutSeconds: number;
  readonly maxIterations: number;
}

export const DEFAULT_CONFIG: PurchaseConfig = Object.freeze({
  openaiRuntime: CODEX_RUNTIME_AUTO,
  requestTimeoutSeconds: 300,
  maxContextCharacters: 120_000,
  searxngBaseUrl: "http://127.0.0.1:8888",
  searxngTimeoutSeconds: 30,
  maxIterations: 500,
});

const FILE_TO_PROPERTY = {
  provider: "provider",
  model: "model",
  openai_runtime: "openaiRuntime",
  database_path: "databasePath",
  request_timeout_seconds: "requestTimeoutSeconds",
  max_context_characters: "maxContextCharacters",
  searxng_base_url: "searxngBaseUrl",
  searxng_timeout_seconds: "searxngTimeoutSeconds",
  max_iterations: "maxIterations",
} as const;

type FileKey = keyof typeof FILE_TO_PROPERTY;

export class PurchaseConfigError extends Error {
  override readonly name = "PurchaseConfigError";
}

export interface PathOptions {
  readonly environ?: Readonly<Record<string, string | undefined>>;
}

export interface ConfigIoOptions extends PathOptions {
  readonly configPath?: string;
}

export function getPurchaseHome(options: PathOptions = {}): string {
  const environ = options.environ ?? process.env;
  const overridden = environ[APP_HOME_ENV]?.trim();
  return overridden ? resolve(expandUser(overridden)) : join(homedir(), ".1688-agent-search");
}

export function resolveSkillRoot(
  cwd?: string,
  options: PathOptions = {},
): string {
  const environ = options.environ ?? process.env;
  const overridden = environ[SKILL_ROOT_ENV]?.trim();
  if (overridden) {
    return resolve(expandUser(overridden));
  }
  if (cwd) {
    const projectSkills = resolve(cwd, "skills");
    try {
      if (statSync(projectSkills).isDirectory()) {
        return projectSkills;
      }
    } catch {
      // Fall through to the installed application Skill directory.
    }
  }
  return join(getPurchaseHome(options), "skills");
}

export function getPurchaseConfigPath(options: PathOptions = {}): string {
  return join(getPurchaseHome(options), "config.json");
}

export function resolvedDatabasePath(
  config: PurchaseConfig,
  options: PathOptions = {},
): string {
  return config.databasePath
    ? resolve(expandUser(config.databasePath))
    : join(getPurchaseHome(options), "sessions.db");
}

export function loadPurchaseConfig(options: ConfigIoOptions = {}): PurchaseConfig {
  const path = options.configPath ?? getPurchaseConfigPath(options);
  if (!existsSync(path)) {
    return DEFAULT_CONFIG;
  }
  let raw: unknown;
  try {
    raw = JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    throw new PurchaseConfigError(`配置文件损坏：${path}`, { cause: error });
  }
  if (!isRecord(raw)) {
    throw new PurchaseConfigError("配置文件顶层必须是 JSON 对象");
  }
  return validatePurchaseConfig(raw);
}

export function savePurchaseConfig(
  config: PurchaseConfig,
  options: ConfigIoOptions = {},
): string {
  validatePurchaseConfig(toFilePayload(config));
  const path = options.configPath ?? getPurchaseConfigPath(options);
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  const temporaryPath = replaceExtension(path, ".tmp");
  writeFileSync(temporaryPath, `${JSON.stringify(toFilePayload(config), undefined, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
  chmodSync(temporaryPath, 0o600);
  renameSync(temporaryPath, path);
  return path;
}

export function withPurchaseModel(config: PurchaseConfig, model: string): PurchaseConfig {
  if (!model.trim()) {
    throw new PurchaseConfigError("model 不能为空");
  }
  return { ...config, provider: config.provider ?? DEFAULT_PROVIDER, model };
}

export function withPurchaseProvider(
  config: PurchaseConfig,
  provider: string,
  model?: string,
): PurchaseConfig {
  if (!(SUPPORTED_PROVIDERS as readonly string[]).includes(provider)) {
    throw new PurchaseConfigError(`不支持的供应商：${provider}`);
  }
  if (model !== undefined && !model.trim()) {
    throw new PurchaseConfigError("model 不能为空");
  }
  if (model === undefined) {
    const { model: _previousModel, ...withoutModel } = config;
    return { ...withoutModel, provider };
  }
  return { ...config, provider, model };
}

export function withCodexRuntime(config: PurchaseConfig, runtime: string): PurchaseConfig {
  if (!(SUPPORTED_CODEX_RUNTIMES as readonly string[]).includes(runtime)) {
    throw new PurchaseConfigError(`不支持的 Codex Runtime：${runtime}`);
  }
  return { ...config, openaiRuntime: runtime as CodexRuntime };
}

function validatePurchaseConfig(raw: Record<string, unknown>): PurchaseConfig {
  const unknown = Object.keys(raw)
    .filter((key) => !(key in FILE_TO_PROPERTY))
    .sort();
  if (unknown.length > 0) {
    throw new PurchaseConfigError(`配置包含未知配置项：${unknown.join(", ")}`);
  }
  for (const key of ["provider", "model", "openai_runtime", "database_path", "searxng_base_url"] as const) {
    const value = raw[key];
    if (value !== undefined && value !== null && typeof value !== "string") {
      throw new PurchaseConfigError(`${key} 必须是字符串`);
    }
  }
  for (const key of ["request_timeout_seconds", "max_context_characters", "searxng_timeout_seconds", "max_iterations"] as const) {
    if (key in raw && (!Number.isInteger(raw[key]) || typeof raw[key] !== "number")) {
      throw new PurchaseConfigError(`${key} 必须是整数`);
    }
  }

  const config: PurchaseConfig = {
    ...DEFAULT_CONFIG,
    ...mapFilePayload(raw),
  };
  if (config.provider !== undefined && !config.provider.trim()) {
    throw new PurchaseConfigError("provider 不能为空");
  }
  if (config.model !== undefined && !config.model.trim()) {
    throw new PurchaseConfigError("model 不能为空");
  }
  if (!(SUPPORTED_CODEX_RUNTIMES as readonly string[]).includes(config.openaiRuntime)) {
    throw new PurchaseConfigError("openai_runtime 必须是 auto 或 codex_app_server");
  }
  for (const [name, value] of [
    ["request_timeout_seconds", config.requestTimeoutSeconds],
    ["max_context_characters", config.maxContextCharacters],
    ["searxng_timeout_seconds", config.searxngTimeoutSeconds],
    ["max_iterations", config.maxIterations],
  ] as const) {
    if (value <= 0) {
      throw new PurchaseConfigError(`${name} 必须大于 0`);
    }
  }
  if (!config.searxngBaseUrl.trim()) {
    throw new PurchaseConfigError("searxng_base_url 不能为空");
  }
  return config;
}

function mapFilePayload(raw: Record<string, unknown>): Partial<PurchaseConfig> {
  const mapped: Record<string, unknown> = {};
  for (const [fileKey, property] of Object.entries(FILE_TO_PROPERTY)) {
    const value = raw[fileKey];
    if (value !== undefined && value !== null) {
      mapped[property] = value;
    }
  }
  return mapped as Partial<PurchaseConfig>;
}

function toFilePayload(config: PurchaseConfig): Record<FileKey, unknown> {
  const payload: Partial<Record<FileKey, unknown>> = {
    openai_runtime: config.openaiRuntime,
    request_timeout_seconds: config.requestTimeoutSeconds,
    max_context_characters: config.maxContextCharacters,
    searxng_base_url: config.searxngBaseUrl,
    searxng_timeout_seconds: config.searxngTimeoutSeconds,
    max_iterations: config.maxIterations,
  };
  if (config.provider !== undefined) payload.provider = config.provider;
  if (config.model !== undefined) payload.model = config.model;
  if (config.databasePath !== undefined) payload.database_path = config.databasePath;
  return payload as Record<FileKey, unknown>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function expandUser(path: string): string {
  if (path === "~") return homedir();
  if (path.startsWith("~/")) return join(homedir(), path.slice(2));
  return path;
}

function replaceExtension(path: string, extension: string): string {
  const lastSlash = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  const lastDot = path.lastIndexOf(".");
  return lastDot > lastSlash ? `${path.slice(0, lastDot)}${extension}` : `${path}${extension}`;
}
