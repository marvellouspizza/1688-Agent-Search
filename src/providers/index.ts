import { existsSync } from "node:fs";
import { delimiter, join } from "node:path";
import { spawnSync } from "node:child_process";

import {
  CODEX_PROVIDER,
  CODEX_RUNTIME_APP_SERVER,
  DEFAULT_MODEL,
  DEFAULT_PROVIDER,
  MODEL_ENV,
  OPENAI_PROVIDER,
  PROVIDER_ENV,
  SUPPORTED_PROVIDERS,
  type PurchaseConfig,
} from "../config.js";
import { loadOpenAiApiKey } from "../credentials.js";
import type { ModelOption, ProviderRuntime } from "../models.js";
import { buildCodexChatGptHeaders, loadLocalCodexChatGptAuth, refreshLocalCodexAuth } from "./codex-auth.js";
import { CodexResponsesProviderAdapter } from "./codex-responses.js";
import { PurchaseInvalidResponse, PurchaseProviderError, PurchaseProviderInterrupted } from "./errors.js";
import { listOpenAiModels, OpenAIResponsesProviderAdapter } from "./openai.js";

export {
  CodexResponsesProviderAdapter,
  OpenAIResponsesProviderAdapter,
  PurchaseInvalidResponse,
  PurchaseProviderError,
  PurchaseProviderInterrupted,
};

const MINIMUM_CODEX_VERSION = [0, 125, 0] as const;
const CODEX_SUBPROCESS_ALWAYS_STRIP = new Set([
  "GH_TOKEN", "GITHUB_TOKEN", "GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY_PATH",
  "GITHUB_APP_INSTALLATION_ID", "GATEWAY_RELAY_ID", "GATEWAY_RELAY_SECRET",
  "GATEWAY_RELAY_DELIVERY_KEY", "GATEWAY_ALLOWED_USERS", "GATEWAY_ALLOW_ALL_USERS",
  "TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN",
  "SLACK_SIGNING_SECRET", "HASS_TOKEN", "EMAIL_PASSWORD", "HERMES_DASHBOARD_SESSION_TOKEN",
  "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET", "DAYTONA_API_KEY",
]);

export interface ResolveProviderOptions {
  readonly cliModel?: string;
  readonly cliProvider?: string;
  readonly environ?: Readonly<Record<string, string | undefined>>;
  readonly credentialOverride?: string;
  readonly codexPath?: string;
}

export function resolvePurchaseProvider(config: PurchaseConfig, options: ResolveProviderOptions = {}): ProviderRuntime {
  const environment = options.environ ?? process.env;
  const provider = options.cliProvider || config.provider || environment[PROVIDER_ENV] || DEFAULT_PROVIDER;
  const configuredModel = options.cliModel || config.model || environment[MODEL_ENV];
  if (!(SUPPORTED_PROVIDERS as readonly string[]).includes(provider)) {
    throw new PurchaseProviderError(`不支持的模型供应商：${provider}`);
  }
  if (provider === OPENAI_PROVIDER) {
    const loaded = loadOpenAiApiKey({ environ: environment });
    const credential = options.credentialOverride?.trim() || loaded.apiKey;
    const credentialSource = options.credentialOverride?.trim() ? "interactive-input" : loaded.source;
    if (!credential) throw new PurchaseProviderError("OpenAI API Key 尚未配置。请运行：as1688 provider");
    return {
      provider,
      model: configuredModel ?? "",
      apiMode: "openai_responses_sse",
      baseUrl: "https://api.openai.com/v1",
      credentialSource,
      credential,
    };
  }

  const codexPath = options.codexPath ?? findExecutable("codex", environment.PATH);
  if (!codexPath) throw new PurchaseProviderError("未找到本机 codex 命令。请先安装 Codex CLI。");
  const login = spawnSync(codexPath, ["login", "status"], {
    encoding: "utf8", timeout: 15_000, stdio: ["ignore", "pipe", "pipe"],
  });
  if (login.error) throw new PurchaseProviderError("检查 Codex 登录状态超时", { cause: login.error });
  const status = `${login.stdout}\n${login.stderr}`.trim().toLowerCase();
  if (login.status !== 0 || !status || status.includes("not logged")) {
    throw new PurchaseProviderError("本机 Codex 尚未登录。请先运行：codex login");
  }
  if (!status.includes("chatgpt")) {
    throw new PurchaseProviderError("当前 Codex 不是 ChatGPT 登录。请运行 codex logout，再运行 codex login 并选择 ChatGPT 登录。");
  }
  const apiMode = config.openaiRuntime === CODEX_RUNTIME_APP_SERVER ? "codex_app_server" : "codex_responses";
  if (apiMode === "codex_app_server") verifyCodexVersion(codexPath);
  return {
    provider,
    model: configuredModel ?? DEFAULT_MODEL,
    apiMode,
    baseUrl: "https://chatgpt.com/backend-api/codex",
    credentialSource: "codex-cli-chatgpt-oauth",
    codexPath,
  };
}

export async function listProviderModels(providerRuntime: ProviderRuntime, timeoutSeconds = 60): Promise<ModelOption[]> {
  if (providerRuntime.provider === CODEX_PROVIDER) return await listCodexModels(providerRuntime, timeoutSeconds);
  if (providerRuntime.provider === OPENAI_PROVIDER) return await listOpenAiModels(providerRuntime, { timeoutSeconds });
  throw new PurchaseProviderError(`不支持的模型供应商：${providerRuntime.provider}`);
}

export async function listCodexModels(providerRuntime: ProviderRuntime, timeoutSeconds = 60): Promise<ModelOption[]> {
  const endpoint = "https://chatgpt.com/backend-api/codex/models?client_version=1.0.0";
  let credentials = loadLocalCodexChatGptAuth();
  let response: Response | undefined;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    response = await fetch(endpoint, {
      headers: buildCodexChatGptHeaders(credentials.accessToken),
      signal: AbortSignal.timeout(timeoutSeconds * 1_000),
    });
    if (response.status === 401 && attempt === 0) {
      credentials = await refreshLocalCodexAuth();
      continue;
    }
    break;
  }
  if (!response?.ok) throw new PurchaseProviderError(`读取 Codex 模型目录失败（HTTP ${response?.status ?? 0}）`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.byteLength > 2_000_000) throw new PurchaseInvalidResponse("Codex 模型目录响应过大");
  let payload: unknown;
  try { payload = JSON.parse(new TextDecoder().decode(bytes)); }
  catch (error) { throw new PurchaseInvalidResponse("Codex 模型目录格式无效", { cause: error }); }
  if (!isRecord(payload) || !Array.isArray(payload.models)) throw new PurchaseInvalidResponse("Codex 模型目录格式无效");
  const sortable: Array<{ rank: number; option: ModelOption }> = [];
  for (const item of payload.models) {
    if (!isRecord(item) || typeof item.slug !== "string" || !item.slug.trim()) continue;
    if (typeof item.visibility === "string" && ["hide", "hidden"].includes(item.visibility.toLowerCase())) continue;
    const model = item.slug.trim();
    sortable.push({
      rank: typeof item.priority === "number" ? Math.trunc(item.priority) : 10_000,
      option: {
        model,
        displayName: String(item.display_name ?? item.displayName ?? model),
        description: String(item.description ?? ""),
        isDefault: model === providerRuntime.model,
        hidden: false,
      },
    });
  }
  sortable.sort((left, right) => left.rank - right.rank || left.option.model.localeCompare(right.option.model));
  const models = sortable.map((entry) => entry.option);
  if (models.length === 0) throw new PurchaseInvalidResponse("Codex 模型目录没有返回可用模型");
  return models;
}

export function buildCodexSubprocessEnvironment(
  environment: Readonly<Record<string, string | undefined>> = process.env,
): NodeJS.ProcessEnv {
  const result: NodeJS.ProcessEnv = {};
  for (const [key, value] of Object.entries(environment)) {
    if (value === undefined || CODEX_SUBPROCESS_ALWAYS_STRIP.has(key)) continue;
    const upper = key.toUpperCase();
    if (key.startsWith("_HERMES_FORCE_")) continue;
    if (upper.startsWith("AUXILIARY_") && (upper.endsWith("_API_KEY") || upper.endsWith("_BASE_URL"))) continue;
    if (upper.startsWith("GATEWAY_RELAY_") && (upper.endsWith("_SECRET") || upper.endsWith("_KEY") || upper.endsWith("_TOKEN"))) continue;
    if (key === "VIRTUAL_ENV" || key === "CONDA_PREFIX") continue;
    result[key] = value;
  }
  result.RUST_LOG = "error";
  return result;
}

function verifyCodexVersion(codexPath: string): void {
  const result = spawnSync(codexPath, ["--version"], { encoding: "utf8", timeout: 15_000, stdio: ["ignore", "pipe", "pipe"] });
  const match = `${result.stdout}\n${result.stderr}`.match(/(\d+)\.(\d+)\.(\d+)/u);
  if (result.error || result.status !== 0 || !match) throw new PurchaseProviderError("无法确认 Codex CLI 版本");
  const installed = match.slice(1).map(Number);
  for (let index = 0; index < 3; index += 1) {
    const current = installed[index] ?? 0;
    const minimum = MINIMUM_CODEX_VERSION[index]!;
    if (current > minimum) return;
    if (current < minimum) {
      throw new PurchaseProviderError(`Codex CLI 版本过旧：${installed.join(".")}；最低需要：${MINIMUM_CODEX_VERSION.join(".")}`);
    }
  }
}

function findExecutable(name: string, pathValue = ""): string | undefined {
  for (const directory of pathValue.split(delimiter)) {
    const candidate = join(directory, name);
    if (existsSync(candidate)) return candidate;
  }
  return undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
