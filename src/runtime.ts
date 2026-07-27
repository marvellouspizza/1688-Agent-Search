import { performance } from "node:perf_hooks";

import { installCodexRuntimeMcp } from "./codex-runtime.js";
import {
  CODEX_PROVIDER,
  OPENAI_PROVIDER,
  resolveSkillRoot,
  type PurchaseConfig,
} from "./config.js";
import type {
  ChatResult,
  ConversationState,
  Message,
  ProviderRuntime,
  ProviderStreamResult,
  ProviderTurnResult,
  PurchaseSession,
} from "./models.js";
import { PurchasePromptBuilder } from "./prompt-builder.js";
import { CodexAppServerProviderAdapter } from "./providers/codex-app-server.js";
import {
  CodexResponsesProviderAdapter,
  OpenAIResponsesProviderAdapter,
  PurchaseProviderError,
  PurchaseProviderInterrupted,
} from "./providers/index.js";
import { PurchaseSessionStore } from "./session-store.js";
import { SkillCatalog } from "./skills/catalog.js";
import { ToolRegistry, type McpToolDefinition } from "./tools/registry.js";
import { buildToolRegistry } from "./tools/web/search.js";

export interface PurchaseProviderAdapter {
  providerRuntime: ProviderRuntime;
  actualModel: string;
  threadId: string | undefined;
  openSession(session: PurchaseSession, history: readonly Message[]): string;
  switchModel(model: string): void;
  interrupt(): void | Promise<void>;
  close(): void | Promise<void>;
  streamModelReply?(options: {
    readonly userInput: string;
    readonly userMessageId: string;
    readonly onStreamStarted: () => void;
    readonly onDelta: (delta: string) => void;
  }): Promise<ProviderStreamResult>;
  runModelTurn?(options: {
    readonly inputItems: readonly Record<string, unknown>[];
    readonly toolDefinitions: readonly McpToolDefinition[];
    readonly onStreamStarted: () => void;
    readonly onDelta: (delta: string) => void;
  }): Promise<ProviderTurnResult>;
}

export interface RuntimePromptBuilder {
  countContextCharacters(history: readonly Message[], userInput: string): number;
}

export interface PurchaseAgentRuntimeOptions {
  readonly config: PurchaseConfig;
  readonly providerRuntime: ProviderRuntime;
  readonly sessionStore: PurchaseSessionStore;
  readonly promptBuilder: RuntimePromptBuilder;
  readonly providerAdapter: PurchaseProviderAdapter;
  readonly toolRegistry: ToolRegistry;
}

export class PurchaseAgentRuntime {
  readonly config: PurchaseConfig;
  providerRuntime: ProviderRuntime;
  readonly sessionStore: PurchaseSessionStore;
  readonly promptBuilder: RuntimePromptBuilder;
  readonly providerAdapter: PurchaseProviderAdapter;
  readonly toolRegistry: ToolRegistry;
  session: PurchaseSession | undefined;
  state: ConversationState = "idle";

  constructor(options: PurchaseAgentRuntimeOptions) {
    this.config = options.config;
    this.providerRuntime = options.providerRuntime;
    this.sessionStore = options.sessionStore;
    this.promptBuilder = options.promptBuilder;
    this.providerAdapter = options.providerAdapter;
    this.toolRegistry = options.toolRegistry;
  }

  createOrRestoreSession(sessionId?: string): PurchaseSession {
    if (this.state !== "idle") throw new Error("只有 IDLE 状态可以创建或恢复 Session");
    const session = this.sessionStore.createOrRestoreSession(sessionId, this.providerRuntime);
    if (session.provider !== this.providerRuntime.provider) {
      throw new Error(`Session 属于供应商 ${session.provider}，当前供应商是 ${this.providerRuntime.provider}。请创建新 Session。`);
    }
    this.sessionStore.acquireSessionLock(session.id);
    const history = this.sessionStore.loadContextMessages(session.id);
    const providerThreadId = this.providerAdapter.openSession(session, history);
    this.sessionStore.attachProviderThread(session.id, providerThreadId, this.providerAdapter.actualModel);
    this.session = this.sessionStore.getSession(session.id);
    return this.session;
  }

  async chat(
    userInput: string,
    sessionId?: string,
    callbacks: {
      readonly onDelta?: (delta: string) => void;
      readonly onThinking?: (active: boolean) => void;
    } = {},
  ): Promise<ChatResult> {
    if (this.state !== "idle") throw new Error(`Agent 当前不是空闲状态：${this.state}`);
    if (!userInput.trim()) throw new Error("用户输入不能为空");
    if (!this.session) this.createOrRestoreSession(sessionId);
    else if (sessionId !== undefined && sessionId !== this.session.id) throw new Error("当前 Agent 已绑定另一个 Session");
    const session = this.session!;
    const onDelta = callbacks.onDelta ?? (() => {});
    const onThinking = callbacks.onThinking ?? (() => {});
    const partial: string[] = [];
    let userMessageId = "";
    let requestId = "";
    this.#transition("preparing");
    try {
      const history = this.sessionStore.loadContextMessages(session.id);
      if (this.promptBuilder.countContextCharacters(history, userInput) > this.config.maxContextCharacters) {
        throw new Error("当前会话上下文已超过第一版上限，请新建 Session 后继续");
      }
      const begun = this.sessionStore.beginRequest(session.id, userInput, this.providerRuntime);
      userMessageId = begun.userMessage.id;
      requestId = begun.requestId;
      this.#transition("requesting");

      const handleStreamStarted = (): void => {
        if (this.state === "requesting") {
          this.#transition("streaming");
          this.sessionStore.markRequestStreaming(requestId);
        }
      };
      const handleDelta = (delta: string): void => {
        onThinking(false);
        partial.push(delta);
        onDelta(delta);
      };

      let providerResult: ProviderStreamResult | ProviderTurnResult;
      if (this.providerAdapter.runModelTurn) {
        providerResult = await this.#runToolLoop({
          userInput,
          requestId,
          onStreamStarted: handleStreamStarted,
          onDelta: handleDelta,
          onThinking,
        });
      } else if (this.providerAdapter.streamModelReply) {
        onThinking(true);
        try {
          providerResult = await this.providerAdapter.streamModelReply({
            userInput,
            userMessageId,
            onStreamStarted: handleStreamStarted,
            onDelta: handleDelta,
          });
        } finally {
          onThinking(false);
        }
      } else {
        throw new PurchaseProviderError("Provider 没有可用的模型请求方法");
      }
      if ((this.state as ConversationState) === "requesting") handleStreamStarted();
      const assistant = this.sessionStore.saveReply({
        sessionId: session.id,
        requestId,
        content: providerResult.content,
        providerRuntime: this.providerRuntime,
        actualModel: providerResult.actualModel,
        usage: providerResult.usage,
        providerThreadId: providerResult.providerThreadId,
      });
      this.providerRuntime = { ...this.providerRuntime, model: providerResult.actualModel };
      this.session = this.sessionStore.getSession(session.id);
      this.#transition("completed");
      return {
        status: "completed",
        sessionId: session.id,
        messageId: assistant.id,
        content: assistant.content,
        provider: assistant.provider,
        model: assistant.model,
        usage: providerResult.usage,
      };
    } catch (error) {
      const interrupted = error instanceof PurchaseProviderInterrupted;
      this.state = interrupted ? "interrupted" : "failed";
      if (requestId && userMessageId) {
        this.sessionStore.failRequest({
          requestId,
          userMessageId,
          status: interrupted ? "interrupted" : "failed",
          error: errorMessage(error),
        });
      }
      return {
        status: interrupted ? "interrupted" : "failed",
        sessionId: session.id,
        messageId: "",
        content: partial.join(""),
        provider: this.providerRuntime.provider,
        model: this.providerRuntime.model,
        usage: { inputTokens: 0, outputTokens: 0, totalTokens: 0 },
        error: errorMessage(error),
      };
    } finally {
      onThinking(false);
      this.state = "idle";
    }
  }

  switchModel(model: string): void {
    if (this.state !== "idle") throw new Error("模型回复期间不能切换模型");
    if (!model.trim()) throw new Error("模型名称不能为空");
    this.providerAdapter.switchModel(model);
    this.providerRuntime = this.providerAdapter.providerRuntime;
    if (this.session && this.providerAdapter.threadId) {
      this.sessionStore.attachProviderThread(this.session.id, this.providerAdapter.threadId, model);
      this.session = this.sessionStore.getSession(this.session.id);
    }
  }

  async stopReply(): Promise<void> {
    await this.providerAdapter.interrupt();
  }

  async close(): Promise<void> {
    try {
      await this.providerAdapter.close();
      await this.toolRegistry.close();
    } finally {
      this.sessionStore.close();
    }
  }

  async #runToolLoop(options: {
    readonly userInput: string;
    readonly requestId: string;
    readonly onStreamStarted: () => void;
    readonly onDelta: (delta: string) => void;
    readonly onThinking: (active: boolean) => void;
  }): Promise<ProviderTurnResult> {
    const runner = this.providerAdapter.runModelTurn!;
    const inputItems: Record<string, unknown>[] = this.sessionStore
      .loadContextMessages(this.session!.id)
      .map((message) => ({ role: message.role, content: message.content }));
    inputItems.push({ role: "user", content: options.userInput });
    const definitions = this.toolRegistry.definitions();
    const seen = new Set<string>();
    let sequence = 0;
    let latest: ProviderTurnResult | undefined;

    for (let iteration = 0; iteration < this.config.maxIterations; iteration += 1) {
      options.onThinking(true);
      try {
        latest = await runner.call(this.providerAdapter, {
          inputItems,
          toolDefinitions: definitions,
          onStreamStarted: options.onStreamStarted,
          onDelta: options.onDelta,
        });
      } finally {
        options.onThinking(false);
      }
      if (latest.toolCalls.length === 0) return latest;
      inputItems.push(...latest.responseItems);
      const numbered = latest.toolCalls.map((call) => {
        sequence += 1;
        const signature = `${call.name}:${stableJson(call.arguments)}`;
        if (seen.has(signature)) throw new PurchaseProviderError("模型重复调用相同工具，已停止");
        seen.add(signature);
        return { sequence, call };
      });
      const dispatched = numbered.length > 1 && numbered.every(({ call }) => this.toolRegistry.isParallelSafe(call.name))
        ? await Promise.all(numbered.map((item) => this.#dispatchTool(item)))
        : await sequentialMap(numbered, (item) => this.#dispatchTool(item));
      for (const item of dispatched) {
        this.sessionStore.appendToolTrace({
          sessionId: this.session!.id,
          requestId: options.requestId,
          sequence: item.sequence,
          callId: item.call.callId,
          name: item.call.name,
          argumentsJson: JSON.stringify(item.call.arguments),
          resultJson: item.output,
          status: item.status,
          durationMs: item.durationMs,
        });
        inputItems.push({ type: "function_call_output", call_id: item.call.callId, output: item.output });
      }
    }

    inputItems.push({
      role: "user",
      content: "You've reached the maximum number of tool-calling iterations allowed. Please provide a final response summarizing what you've found and accomplished so far, without calling any more tools.",
    });
    let latestSummary: ProviderTurnResult | undefined;
    try {
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const buffered: string[] = [];
        options.onThinking(true);
        try {
          latestSummary = await runner.call(this.providerAdapter, {
            inputItems,
            toolDefinitions: [],
            onStreamStarted: options.onStreamStarted,
            onDelta: (delta) => buffered.push(delta),
          });
        } finally {
          options.onThinking(false);
        }
        if (latestSummary.toolCalls.length === 0 && latestSummary.content.trim()) {
          if (buffered.length > 0) buffered.forEach(options.onDelta);
          else options.onDelta(latestSummary.content);
          return latestSummary;
        }
      }
      return this.#summaryFallback(latestSummary ?? latest!, "I reached the iteration limit and couldn't generate a summary.", options.onDelta);
    } catch (error) {
      if (error instanceof PurchaseProviderInterrupted) throw error;
      return this.#summaryFallback(
        latestSummary ?? latest!,
        `I reached the maximum iterations (${this.config.maxIterations}) but couldn't summarize. Error: ${errorMessage(error).slice(0, 1_000)}`,
        options.onDelta,
      );
    }
  }

  async #dispatchTool(item: { sequence: number; call: ProviderTurnResult["toolCalls"][number] }): Promise<{
    readonly sequence: number;
    readonly call: ProviderTurnResult["toolCalls"][number];
    readonly output: string;
    readonly status: "completed" | "failed";
    readonly durationMs: number;
  }> {
    const started = performance.now();
    try {
      const result = await this.toolRegistry.dispatch(item.call.name, item.call.arguments);
      return { ...item, output: boundedJson(result), status: "completed", durationMs: Math.round(performance.now() - started) };
    } catch (error) {
      return {
        ...item,
        output: boundedJson({ error: errorMessage(error).slice(0, 1_000) }),
        status: "failed",
        durationMs: Math.round(performance.now() - started),
      };
    }
  }

  #summaryFallback(base: ProviderTurnResult, content: string, onDelta: (delta: string) => void): ProviderTurnResult {
    onDelta(content);
    return { ...base, content, toolCalls: [], responseItems: [] };
  }

  #transition(next: ConversationState): void {
    const allowed: Record<ConversationState, readonly ConversationState[]> = {
      idle: ["preparing"],
      preparing: ["requesting", "failed"],
      requesting: ["streaming", "failed"],
      streaming: ["completed", "failed", "interrupted", "incomplete"],
      completed: ["idle"],
      failed: ["idle"],
      interrupted: ["idle"],
      incomplete: ["idle"],
    };
    if (!allowed[this.state].includes(next)) throw new Error(`非法状态变化：${this.state} → ${next}`);
    this.state = next;
  }
}

export function createPurchaseAgent(options: {
  readonly config: PurchaseConfig;
  readonly providerRuntime: ProviderRuntime;
  readonly sessionStore: PurchaseSessionStore;
  readonly cwd: string;
}): PurchaseAgentRuntime {
  const skillRoot = resolveSkillRoot(options.cwd);
  const promptBuilder = new PurchasePromptBuilder({ skillCatalog: new SkillCatalog([skillRoot]) });
  const toolRegistry = buildToolRegistry({ skillRoot, config: options.config });
  let providerAdapter: PurchaseProviderAdapter;
  if (options.providerRuntime.provider === CODEX_PROVIDER) {
    if (options.providerRuntime.apiMode === "codex_app_server") {
      installCodexRuntimeMcp({ cwd: options.cwd, skillRoot });
      providerAdapter = new CodexAppServerProviderAdapter(options.providerRuntime, options.config, { cwd: options.cwd });
    } else {
      providerAdapter = new CodexResponsesProviderAdapter(options.providerRuntime, options.config, promptBuilder);
    }
  } else if (options.providerRuntime.provider === OPENAI_PROVIDER) {
    providerAdapter = new OpenAIResponsesProviderAdapter(options.providerRuntime, options.config, promptBuilder);
  } else {
    throw new PurchaseProviderError(`不支持的模型供应商：${options.providerRuntime.provider}`);
  }
  return new PurchaseAgentRuntime({
    ...options,
    promptBuilder,
    providerAdapter,
    toolRegistry,
  });
}

async function sequentialMap<T, R>(items: readonly T[], operation: (item: T) => Promise<R>): Promise<R[]> {
  const results: R[] = [];
  for (const item of items) results.push(await operation(item));
  return results;
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (typeof value === "object" && value !== null) {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${stableJson(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function boundedJson(value: unknown): string {
  return (JSON.stringify(value) ?? "null").slice(0, 30_000);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
