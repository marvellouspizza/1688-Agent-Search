import type { PurchaseConfig } from "../config.js";
import type {
  Message,
  ProviderRuntime,
  ProviderToolCall,
  ProviderTurnResult,
  PurchaseSession,
} from "../models.js";
import type { McpToolDefinition } from "../tools/registry.js";
import {
  buildCodexChatGptHeaders,
  loadLocalCodexChatGptAuth,
  refreshLocalCodexAuth,
  type CodexAuth,
} from "./codex-auth.js";
import { PurchaseInvalidResponse, PurchaseProviderError, PurchaseProviderInterrupted } from "./errors.js";
import { iterateSseEvents, responseUsage, type ProviderPromptBuilder } from "./openai.js";

export const CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses";

interface CodexAuthAdapter {
  load(): CodexAuth;
  refresh(): Promise<CodexAuth>;
  headers(accessToken: string): Record<string, string>;
}

export interface CodexResponsesOptions {
  readonly fetchImpl?: typeof fetch;
  readonly auth?: CodexAuthAdapter;
}

export function responsesTools(definitions: readonly McpToolDefinition[]): Record<string, unknown>[] {
  return definitions.map((item) => ({
    type: "function",
    name: item.name,
    description: item.description,
    parameters: item.inputSchema,
  }));
}

export class CodexResponsesProviderAdapter {
  providerRuntime: ProviderRuntime;
  actualModel: string;
  threadId: string | undefined;
  readonly #config: PurchaseConfig;
  readonly #promptBuilder: ProviderPromptBuilder;
  readonly #fetch: typeof fetch;
  readonly #auth: CodexAuthAdapter;
  #instructions = "";
  #activeController: AbortController | undefined;

  constructor(
    providerRuntime: ProviderRuntime,
    config: PurchaseConfig,
    promptBuilder: ProviderPromptBuilder,
    options: CodexResponsesOptions = {},
  ) {
    this.providerRuntime = providerRuntime;
    this.actualModel = providerRuntime.model;
    this.#config = config;
    this.#promptBuilder = promptBuilder;
    this.#fetch = options.fetchImpl ?? fetch;
    this.#auth = options.auth ?? {
      load: () => loadLocalCodexChatGptAuth(),
      refresh: () => refreshLocalCodexAuth(),
      headers: buildCodexChatGptHeaders,
    };
  }

  openSession(session: PurchaseSession, _history: readonly Message[]): string {
    this.threadId = session.providerThreadId?.startsWith("resp_")
      ? session.providerThreadId
      : `codex_local_${session.id}`;
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

  interrupt(): void {
    this.#activeController?.abort();
  }

  close(): void {
    this.interrupt();
  }

  async runModelTurn(options: {
    readonly inputItems: readonly Record<string, unknown>[];
    readonly toolDefinitions: readonly McpToolDefinition[];
    readonly onStreamStarted: () => void;
    readonly onDelta: (delta: string) => void;
  }): Promise<ProviderTurnResult> {
    if (!this.threadId) throw new PurchaseProviderError("Provider Session 尚未创建");
    const payload: Record<string, unknown> = {
      model: this.providerRuntime.model,
      instructions: this.#instructions,
      input: options.inputItems,
      store: false,
      stream: true,
    };
    const tools = responsesTools(options.toolDefinitions);
    if (tools.length > 0) {
      payload.tools = tools;
      payload.tool_choice = "auto";
      payload.parallel_tool_calls = true;
    }
    const response = await this.#request(payload, options.onStreamStarted);
    if (!Array.isArray(response.output)) throw new PurchaseInvalidResponse("Codex Responses 未返回 output 列表");
    const calls: ProviderToolCall[] = [];
    const textParts: string[] = [];
    const responseItems: Record<string, unknown>[] = [];
    for (const rawItem of response.output) {
      if (!isRecord(rawItem)) continue;
      if (rawItem.type === "function_call") {
        if (typeof rawItem.call_id !== "string" || typeof rawItem.name !== "string" || typeof rawItem.arguments !== "string") {
          throw new PurchaseInvalidResponse("Codex function_call 格式无效");
        }
        let arguments_: unknown;
        try {
          arguments_ = JSON.parse(rawItem.arguments);
        } catch (error) {
          throw new PurchaseInvalidResponse("Codex function_call 参数不是 JSON", { cause: error });
        }
        if (!isRecord(arguments_)) throw new PurchaseInvalidResponse("Codex function_call 参数必须是对象");
        calls.push({ callId: rawItem.call_id, name: rawItem.name, arguments: arguments_ });
        responseItems.push({ type: "function_call", call_id: rawItem.call_id, name: rawItem.name, arguments: rawItem.arguments });
      } else if (rawItem.type === "message") {
        responseItems.push(rawItem);
        if (Array.isArray(rawItem.content)) {
          for (const part of rawItem.content) {
            if (isRecord(part) && part.type === "output_text" && typeof part.text === "string") textParts.push(part.text);
          }
        }
      }
    }
    const content = textParts.join("");
    if (content) options.onDelta(content);
    const responseId = typeof response.id === "string" && response.id ? response.id : this.threadId;
    const actualModel = typeof response.model === "string" && response.model ? response.model : this.providerRuntime.model;
    this.actualModel = actualModel;
    this.threadId = responseId;
    return {
      content,
      toolCalls: calls,
      responseItems,
      usage: responseUsage(isRecord(response.usage) ? response.usage : {}),
      actualModel,
      responseId,
      providerThreadId: responseId,
    };
  }

  async #request(payload: Record<string, unknown>, onStreamStarted: () => void): Promise<Record<string, unknown>> {
    let credentials = this.#auth.load();
    let refreshed = false;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const controller = new AbortController();
      this.#activeController = controller;
      const timeout = setTimeout(() => controller.abort(new Error("Codex Responses 请求超时")), this.#config.requestTimeoutSeconds * 1_000);
      timeout.unref();
      let response: Response;
      try {
        response = await this.#fetch(CODEX_RESPONSES_URL, {
          method: "POST",
          headers: this.#auth.headers(credentials.accessToken),
          body: JSON.stringify(payload),
          signal: controller.signal,
        });
      } catch (error) {
        clearTimeout(timeout);
        if (controller.signal.aborted) throw new PurchaseProviderInterrupted("用户已停止回复", { cause: error });
        throw new PurchaseProviderError(`无法连接 Codex Responses：${safeError(error)}`, { cause: error });
      }
      if (response.status === 401) {
        clearTimeout(timeout);
        const fresh = this.#auth.load();
        if (fresh.accessToken !== credentials.accessToken) {
          credentials = fresh;
          continue;
        }
        if (!refreshed) {
          credentials = await this.#auth.refresh();
          refreshed = true;
          continue;
        }
      }
      if (!response.ok) {
        clearTimeout(timeout);
        throw new PurchaseProviderError(await codexHttpError(response));
      }
      onStreamStarted();
      try {
        const completed = await consumeCodexStream(response);
        if (completed.status !== undefined && completed.status !== "completed") {
          throw new PurchaseProviderError("Codex Responses 请求未成功完成");
        }
        return completed;
      } finally {
        clearTimeout(timeout);
        if (this.#activeController === controller) this.#activeController = undefined;
      }
    }
    throw new PurchaseProviderError("Codex Responses 身份验证失败，请运行：codex login");
  }
}

async function consumeCodexStream(response: Response): Promise<Record<string, unknown>> {
  let completed: Record<string, unknown> | undefined;
  const outputItems: Record<string, unknown>[] = [];
  const textDeltas: string[] = [];
  for await (const event of iterateSseEvents(response)) {
    if (event.type === "error") {
      throw new PurchaseProviderError(typeof event.message === "string" ? event.message : "Codex Responses 流返回错误");
    }
    if (event.type === "response.output_item.done" && isRecord(event.item)) {
      outputItems.push(event.item);
    } else if (event.type === "response.output_text.delta" && typeof event.delta === "string") {
      textDeltas.push(event.delta);
    } else if (event.type === "response.completed" && isRecord(event.response)) {
      completed = { ...event.response };
    }
  }
  if (!completed) throw new PurchaseInvalidResponse("Codex Responses 流未返回完成事件");
  if (outputItems.length > 0) {
    completed.output = outputItems;
  } else if (textDeltas.length > 0) {
    completed.output = [{ type: "message", role: "assistant", content: [{ type: "output_text", text: textDeltas.join("") }] }];
  } else if (!Array.isArray(completed.output)) {
    completed.output = [];
  }
  return completed;
}

async function codexHttpError(response: Response): Promise<string> {
  let detail = "";
  try {
    const raw = (await response.text()).slice(0, 8_000);
    const parsed: unknown = JSON.parse(raw);
    if (isRecord(parsed) && isRecord(parsed.error) && typeof parsed.error.message === "string") detail = parsed.error.message;
    else detail = raw;
  } catch {
    // Omit malformed response details.
  }
  return `Codex Responses 请求失败（HTTP ${response.status}）${detail ? `：${detail.slice(0, 500)}` : ""}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function safeError(error: unknown): string {
  return error instanceof Error ? error.message.slice(0, 500) : String(error).slice(0, 500);
}
