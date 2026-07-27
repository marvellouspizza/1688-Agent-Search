import type { PurchaseConfig } from "../config.js";
import type {
  Message,
  ModelOption,
  ProviderRuntime,
  ProviderStreamResult,
  PurchaseSession,
  TokenUsage,
} from "../models.js";
import { PurchaseInvalidResponse, PurchaseProviderError, PurchaseProviderInterrupted } from "./errors.js";

const OPENAI_TEXT_MODEL_PREFIXES = ["gpt-", "o1", "o3", "o4"] as const;
const OPENAI_NON_TEXT_MARKERS = [
  "audio", "computer-use", "deep-research", "embedding", "image", "instruct",
  "moderation", "realtime", "search", "transcribe", "tts", "whisper",
] as const;

export interface ProviderPromptBuilder {
  buildBaseInstructions(): string;
  buildContext(sessionId: string, providerRuntime: ProviderRuntime): string;
}

export interface FetchOptions {
  readonly fetchImpl?: typeof fetch;
}

export async function listOpenAiModels(
  providerRuntime: ProviderRuntime,
  options: FetchOptions & { readonly timeoutSeconds?: number } = {},
): Promise<ModelOption[]> {
  if (!providerRuntime.credential) throw new PurchaseProviderError("OpenAI API Key 尚未配置");
  let response: Response;
  try {
    response = await (options.fetchImpl ?? fetch)(`${providerRuntime.baseUrl}/models`, {
      headers: openAiHeaders(providerRuntime.credential),
      signal: AbortSignal.timeout((options.timeoutSeconds ?? 60) * 1_000),
    });
  } catch (error) {
    throw new PurchaseProviderError(`无法连接 OpenAI API：${safeError(error)}`, { cause: error });
  }
  if (!response.ok) throw new PurchaseProviderError(await safeOpenAiHttpError(response));
  const raw = await readBoundedBytes(response, 5_000_000, "OpenAI 模型目录响应过大");
  let payload: unknown;
  try {
    payload = JSON.parse(new TextDecoder().decode(raw));
  } catch (error) {
    throw new PurchaseInvalidResponse("OpenAI 模型目录格式无效", { cause: error });
  }
  if (!isRecord(payload) || !Array.isArray(payload.data)) {
    throw new PurchaseInvalidResponse("OpenAI 未返回模型目录");
  }
  const models: ModelOption[] = [];
  for (const item of payload.data) {
    if (!isRecord(item) || typeof item.id !== "string" || !isOpenAiTextModel(item.id)) continue;
    models.push({
      model: item.id,
      displayName: item.id,
      description: typeof item.owned_by === "string" && item.owned_by
        ? `OpenAI API 模型 · ${item.owned_by}`
        : "OpenAI API 模型",
      isDefault: false,
      hidden: false,
    });
  }
  models.sort((left, right) => right.model.localeCompare(left.model));
  if (models.length === 0) throw new PurchaseProviderError("当前 OpenAI 账号没有返回可用的文本模型");
  return models;
}

export async function* iterateSseEvents(response: Response): AsyncGenerator<Record<string, unknown>> {
  if (!response.body) throw new PurchaseInvalidResponse("OpenAI SSE 响应没有正文");
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "";
  let dataLines: string[] = [];
  while (true) {
    let decoded: string;
    try {
      const { done, value } = await reader.read();
      decoded = decoder.decode(value, { stream: !done });
      buffer += decoded;
      if (done) break;
    } catch (error) {
      if (isAbortError(error)) throw new PurchaseProviderInterrupted("用户已中止模型请求", { cause: error });
      throw new PurchaseInvalidResponse("OpenAI SSE 流不是有效 UTF-8", { cause: error });
    }
    let newline: number;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      let line = buffer.slice(0, newline);
      buffer = buffer.slice(newline + 1);
      if (line.endsWith("\r")) line = line.slice(0, -1);
      if (line === "") {
        const event = parseSseData(dataLines);
        dataLines = [];
        if (event) yield event;
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }
  }
  if (buffer) {
    const line = buffer.endsWith("\r") ? buffer.slice(0, -1) : buffer;
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (dataLines.length > 0 && dataLines.join("\n") !== "[DONE]") {
    throw new PurchaseInvalidResponse("OpenAI SSE 流没有正常结束");
  }
}

export class OpenAIResponsesProviderAdapter {
  providerRuntime: ProviderRuntime;
  actualModel: string;
  threadId: string | undefined;
  readonly #config: PurchaseConfig;
  readonly #promptBuilder: ProviderPromptBuilder;
  readonly #fetch: typeof fetch;
  #history: Array<{ role: string; content: string }> = [];
  #instructions = "";
  #activeController: AbortController | undefined;

  constructor(
    providerRuntime: ProviderRuntime,
    config: PurchaseConfig,
    promptBuilder: ProviderPromptBuilder,
    options: FetchOptions = {},
  ) {
    if (!providerRuntime.credential) throw new PurchaseProviderError("OpenAI API Key 尚未配置");
    this.providerRuntime = providerRuntime;
    this.actualModel = providerRuntime.model;
    this.#config = config;
    this.#promptBuilder = promptBuilder;
    this.#fetch = options.fetchImpl ?? fetch;
  }

  openSession(session: PurchaseSession, history: readonly Message[]): string {
    this.threadId = session.provider === this.providerRuntime.provider && session.providerThreadId
      ? session.providerThreadId
      : `openai_local_${session.id}`;
    this.#history = history.map((message) => ({ role: message.role, content: message.content }));
    this.#instructions = [
      this.#promptBuilder.buildBaseInstructions(),
      this.#promptBuilder.buildContext(session.id, this.providerRuntime),
    ].join("\n\n");
    return this.threadId;
  }

  switchModel(model: string): void {
    this.providerRuntime = { ...this.providerRuntime, model };
    this.actualModel = model;
  }

  async streamModelReply(options: {
    readonly userInput: string;
    readonly userMessageId: string;
    readonly onStreamStarted: () => void;
    readonly onDelta: (delta: string) => void;
  }): Promise<ProviderStreamResult> {
    void options.userMessageId;
    if (!this.threadId) throw new PurchaseProviderError("Provider Session 尚未创建");
    if (!this.providerRuntime.credential) throw new PurchaseProviderError("OpenAI API Key 尚未配置");
    const controller = new AbortController();
    this.#activeController = controller;
    let timedOut = false;
    const timeout = setTimeout(() => {
      timedOut = true;
      controller.abort(new Error("OpenAI 请求超时"));
    }, this.#config.requestTimeoutSeconds * 1_000);
    timeout.unref();
    let response: Response;
    try {
      response = await this.#fetch(`${this.providerRuntime.baseUrl}/responses`, {
        method: "POST",
        headers: openAiHeaders(this.providerRuntime.credential),
        body: JSON.stringify({
          model: this.providerRuntime.model,
          instructions: this.#instructions,
          input: [...this.#history, { role: "user", content: options.userInput }],
          stream: true,
          store: false,
          tools: [],
          tool_choice: "none",
          parallel_tool_calls: false,
        }),
        signal: controller.signal,
      });
      if (!response.ok) throw new PurchaseProviderError(await safeOpenAiHttpError(response));
      options.onStreamStarted();
      const result = await this.#consumeTextStream(response, options.onDelta);
      this.#history.push(
        { role: "user", content: options.userInput },
        { role: "assistant", content: result.content },
      );
      this.actualModel = result.actualModel;
      return { ...result, providerThreadId: this.threadId };
    } catch (error) {
      if (error instanceof PurchaseProviderError) throw error;
      if (timedOut) throw new PurchaseProviderError("OpenAI 请求超时", { cause: error });
      if (controller.signal.aborted || isAbortError(error)) {
        throw new PurchaseProviderInterrupted("用户已中止模型请求", { cause: error });
      }
      throw new PurchaseProviderError(`无法连接 OpenAI API：${safeError(error)}`, { cause: error });
    } finally {
      clearTimeout(timeout);
      if (this.#activeController === controller) this.#activeController = undefined;
    }
  }

  interrupt(): void {
    this.#activeController?.abort();
  }

  close(): void {
    this.interrupt();
  }

  async #consumeTextStream(response: Response, onDelta: (delta: string) => void): Promise<{
    readonly content: string;
    readonly usage: TokenUsage;
    readonly actualModel: string;
  }> {
    const parts: string[] = [];
    let fallbackText: string | undefined;
    let completed: Record<string, unknown> | undefined;
    for await (const event of iterateSseEvents(response)) {
      const type = event.type;
      if (type === "response.output_text.delta" || type === "response.refusal.delta") {
        if (typeof event.delta !== "string") throw new PurchaseInvalidResponse("OpenAI 文字增量格式无效");
        parts.push(event.delta);
        onDelta(event.delta);
      } else if (type === "response.output_text.done" && typeof event.text === "string") {
        fallbackText = event.text;
      } else if (type === "response.refusal.done" && typeof event.refusal === "string") {
        fallbackText = event.refusal;
      } else if ([
        "response.created", "response.in_progress", "response.queued", "response.content_part.added",
        "response.content_part.done", "response.output_text.annotation.added",
      ].includes(String(type)) || (typeof type === "string" && type.startsWith("response.reasoning"))) {
        continue;
      } else if (type === "response.output_item.added" || type === "response.output_item.done") {
        const itemType = isRecord(event.item) ? event.item.type : undefined;
        if (itemType !== "message" && itemType !== "reasoning") {
          throw new PurchaseInvalidResponse(`普通对话拒绝 OpenAI 输出项：${String(itemType)}`);
        }
      } else if (type === "response.completed") {
        if (!isRecord(event.response)) throw new PurchaseInvalidResponse("OpenAI 完成事件格式无效");
        completed = event.response;
      } else if (type === "response.failed" || type === "response.incomplete" || type === "error") {
        const message = isRecord(event.error) && typeof event.error.message === "string" ? event.error.message : undefined;
        throw new PurchaseProviderError(message ?? "OpenAI 请求未成功完成");
      } else {
        throw new PurchaseInvalidResponse(`收到尚未审计的 OpenAI 流事件：${String(type)}`);
      }
    }
    if (!completed) throw new PurchaseInvalidResponse("OpenAI 流缺少 response.completed");
    if (completed.status !== "completed") throw new PurchaseProviderError("OpenAI Response 未成功完成");
    const content = parts.join("") || fallbackText || "";
    if (!content.trim()) throw new PurchaseInvalidResponse("OpenAI 已完成，但没有返回有效文字");
    const usage = isRecord(completed.usage) ? completed.usage : {};
    return {
      content,
      usage: responseUsage(usage),
      actualModel: typeof completed.model === "string" && completed.model ? completed.model : this.providerRuntime.model,
    };
  }
}

export function openAiHeaders(apiKey: string): Record<string, string> {
  return {
    Authorization: `Bearer ${apiKey}`,
    "Content-Type": "application/json",
    "User-Agent": "as1688/1.0.0",
  };
}

export function responseUsage(usage: Record<string, unknown>): TokenUsage {
  return {
    inputTokens: integerOrZero(usage.input_tokens),
    outputTokens: integerOrZero(usage.output_tokens),
    totalTokens: integerOrZero(usage.total_tokens),
  };
}

async function safeOpenAiHttpError(response: Response): Promise<string> {
  if (response.status === 401) return "OpenAI API Key 无效或已失效。请运行：as1688 provider --update-key";
  if (response.status === 429) return "OpenAI API 请求过于频繁、余额不足或额度已用完";
  let suffix = "";
  try {
    const raw = await readBoundedBytes(response, 16_384, "");
    const payload: unknown = JSON.parse(new TextDecoder().decode(raw));
    if (isRecord(payload) && isRecord(payload.error) && typeof payload.error.message === "string") {
      suffix = `：${payload.error.message.slice(0, 500)}`;
    }
  } catch {
    // Omit unaudited error bodies.
  }
  return `OpenAI API 请求失败（HTTP ${response.status}）${suffix}`;
}

async function readBoundedBytes(response: Response, maximum: number, oversizedMessage: string): Promise<Uint8Array> {
  if (!response.body) return new Uint8Array();
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > maximum) {
      await reader.cancel();
      throw new PurchaseInvalidResponse(oversizedMessage || "响应过大");
    }
    chunks.push(value);
  }
  const result = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return result;
}

function parseSseData(lines: readonly string[]): Record<string, unknown> | undefined {
  if (lines.length === 0) return undefined;
  const data = lines.join("\n");
  if (data === "[DONE]") return undefined;
  let value: unknown;
  try {
    value = JSON.parse(data);
  } catch (error) {
    throw new PurchaseInvalidResponse("OpenAI SSE 事件不是有效 JSON", { cause: error });
  }
  if (!isRecord(value)) throw new PurchaseInvalidResponse("OpenAI SSE 事件格式无效");
  return value;
}

function isOpenAiTextModel(model: string): boolean {
  const lowered = model.toLowerCase();
  return OPENAI_TEXT_MODEL_PREFIXES.some((prefix) => lowered.startsWith(prefix))
    && !OPENAI_NON_TEXT_MARKERS.some((marker) => lowered.includes(marker));
}

function integerOrZero(value: unknown): number {
  const numeric = Number(value ?? 0);
  return Number.isFinite(numeric) ? Math.trunc(numeric) : 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isAbortError(error: unknown): boolean {
  return (error as { name?: unknown })?.name === "AbortError";
}

function safeError(error: unknown): string {
  return error instanceof Error ? error.message.slice(0, 500) : String(error).slice(0, 500);
}
