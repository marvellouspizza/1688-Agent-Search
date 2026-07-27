import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface } from "node:readline";
import { createInterface as createPrompts } from "node:readline/promises";

import type { PurchaseConfig } from "../config.js";
import {
  tokenUsageFromCodex,
  type Message,
  type ProviderRuntime,
  type ProviderStreamResult,
  type PurchaseSession,
  type TokenUsage,
} from "../models.js";
import { buildCodexSubprocessEnvironment } from "./index.js";
import { PurchaseInvalidResponse, PurchaseProviderError, PurchaseProviderInterrupted } from "./errors.js";

type JsonObject = Record<string, unknown>;
type ServerRequestHandler = (request: JsonObject) => void | Promise<void>;

interface PendingRequest {
  readonly method: string;
  readonly resolve: (result: JsonObject) => void;
  readonly reject: (error: Error) => void;
  readonly timer: NodeJS.Timeout;
}

export interface CodexAppServerTransportOptions {
  readonly command: string;
  readonly args?: readonly string[];
  readonly timeoutSeconds: number;
  readonly environment?: Readonly<Record<string, string | undefined>>;
  readonly serverRequestHandler?: ServerRequestHandler;
}

export class CodexAppServerTransport {
  readonly #options: CodexAppServerTransportOptions;
  readonly #pending = new Map<number, PendingRequest>();
  readonly #notifications = new AsyncMessageQueue();
  readonly #stderrTail: string[] = [];
  #process: ChildProcessWithoutNullStreams | undefined;
  #requestId = 0;
  #serverRequestHandler: ServerRequestHandler | undefined;
  #starting: Promise<void> | undefined;

  constructor(options: CodexAppServerTransportOptions) {
    this.#options = options;
    this.#serverRequestHandler = options.serverRequestHandler;
  }

  get started(): boolean {
    return this.#process !== undefined && this.#process.exitCode === null;
  }

  setServerRequestHandler(handler: ServerRequestHandler): void {
    this.#serverRequestHandler = handler;
  }

  async start(): Promise<void> {
    if (this.started) return;
    if (this.#starting) return await this.#starting;
    this.#starting = this.#startOnce();
    try { await this.#starting; }
    finally { this.#starting = undefined; }
  }

  async request(method: string, params: JsonObject, timeoutSeconds?: number): Promise<JsonObject> {
    if (!this.started && method !== "initialize") throw new PurchaseProviderError("Codex app-server 未运行");
    const id = ++this.#requestId;
    const timeout = (timeoutSeconds ?? this.#options.timeoutSeconds) * 1_000;
    const promise = new Promise<JsonObject>((resolvePromise, rejectPromise) => {
      const timer = setTimeout(() => {
        this.#pending.delete(id);
        rejectPromise(new PurchaseProviderError(`等待 ${method} 响应超时`));
      }, timeout);
      timer.unref();
      this.#pending.set(id, { method, resolve: resolvePromise, reject: rejectPromise, timer });
    });
    try {
      this.#write({ method, id, params });
    } catch (error) {
      const pending = this.#pending.get(id);
      if (pending) clearTimeout(pending.timer);
      this.#pending.delete(id);
      throw error;
    }
    return await promise;
  }

  notify(method: string, params: JsonObject): void {
    this.#write({ method, params });
  }

  async nextMessage(timeoutSeconds?: number): Promise<JsonObject> {
    return await this.#notifications.next((timeoutSeconds ?? this.#options.timeoutSeconds) * 1_000);
  }

  respond(id: unknown, result: JsonObject): void {
    this.#write({ id, result });
  }

  respondError(id: unknown, code: number, message: string): void {
    this.#write({ id, error: { code, message } });
  }

  async close(): Promise<void> {
    const child = this.#process;
    this.#process = undefined;
    if (!child) return;
    child.stdin.end();
    if (child.exitCode === null) {
      const exited = new Promise<void>((resolvePromise) => child.once("exit", () => resolvePromise()));
      if (!await settleWithin(exited, 2_000)) {
        child.kill("SIGTERM");
        if (!await settleWithin(exited, 2_000)) child.kill("SIGKILL");
      }
    }
    this.#rejectPending(new PurchaseProviderError("Codex app-server 已关闭"));
    this.#notifications.end(new PurchaseProviderError("Codex app-server 已关闭"));
  }

  async #startOnce(): Promise<void> {
    try {
      const child = spawn(this.#options.command, [...(this.#options.args ?? ["app-server"])], {
        env: buildCodexSubprocessEnvironment(this.#options.environment ?? process.env),
        stdio: ["pipe", "pipe", "pipe"],
      });
      this.#process = child;
      createInterface({ input: child.stdout }).on("line", (line) => this.#consumeLine(line));
      createInterface({ input: child.stderr }).on("line", (line) => {
        this.#stderrTail.push(line.slice(0, 2_000));
        if (this.#stderrTail.length > 20) this.#stderrTail.shift();
      });
      child.once("error", (error) => this.#handleExit(new PurchaseProviderError("无法启动 Codex app-server", { cause: error })));
      child.once("exit", (code) => this.#handleExit(new PurchaseProviderError(`Codex app-server 已退出（状态码：${String(code)}）`)));
      await this.request("initialize", {
        clientInfo: { name: "agent_search_1688", title: "1688 Agent Search", version: "1.0.0" },
      });
      this.notify("initialized", {});
    } catch (error) {
      await this.close();
      if (error instanceof PurchaseProviderError) throw error;
      throw new PurchaseProviderError("无法启动 Codex app-server", { cause: error });
    }
  }

  #consumeLine(line: string): void {
    let message: unknown;
    try { message = JSON.parse(line); }
    catch { return; }
    if (!isRecord(message)) return;
    if ("id" in message && !("method" in message)) {
      const id = typeof message.id === "number" ? message.id : Number(message.id);
      const pending = this.#pending.get(id);
      if (!pending) {
        this.#notifications.push(message);
        return;
      }
      this.#pending.delete(id);
      clearTimeout(pending.timer);
      if ("error" in message) {
        const detail = isRecord(message.error) && typeof message.error.message === "string" ? message.error.message : "未知错误";
        pending.reject(new PurchaseProviderError(`Codex ${pending.method} 失败：${detail}`));
      } else {
        pending.resolve(isRecord(message.result) ? message.result : {});
      }
      return;
    }
    if ("method" in message && "id" in message) {
      void this.#handleServerRequest(message);
      return;
    }
    this.#notifications.push(message);
  }

  async #handleServerRequest(message: JsonObject): Promise<void> {
    if (!this.#serverRequestHandler) {
      this.respondError(message.id, -32601, `不支持的 Codex Server 请求：${String(message.method ?? "")}`);
      return;
    }
    try { await this.#serverRequestHandler(message); }
    catch (error) { this.respondError(message.id, -32603, error instanceof Error ? error.message : String(error)); }
  }

  #write(message: JsonObject): void {
    const child = this.#process;
    if (!child || child.exitCode !== null || child.stdin.destroyed) throw new PurchaseProviderError("Codex app-server 未运行");
    child.stdin.write(`${JSON.stringify(message)}\n`, "utf8", (error) => {
      if (error) this.#handleExit(new PurchaseProviderError("Codex app-server 连接已断开", { cause: error }));
    });
  }

  #handleExit(error: Error): void {
    this.#rejectPending(error);
    this.#notifications.end(error);
  }

  #rejectPending(error: Error): void {
    for (const pending of this.#pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.#pending.clear();
  }
}

export function buildCodexTurnRequest(threadId: string, userInput: string): JsonObject {
  return { threadId, input: [{ type: "text", text: userInput }] };
}

export class CodexStreamCollector {
  readonly #threadId: string;
  readonly #turnId: string;
  readonly parts: string[] = [];
  model: string;
  usage: TokenUsage = { inputTokens: 0, outputTokens: 0, totalTokens: 0 };
  completed = false;
  #finalText: string | undefined;
  #turnStatus: string | undefined;
  #error: string | undefined;

  constructor(threadId: string, turnId: string, model: string) {
    this.#threadId = threadId;
    this.#turnId = turnId;
    this.model = model;
  }

  consume(message: JsonObject): string | undefined {
    if (!isRecord(message.params)) return undefined;
    const params = message.params;
    if (params.threadId !== undefined && params.threadId !== this.#threadId) return undefined;
    if (params.turnId !== undefined && params.turnId !== this.#turnId) {
      if (!isRecord(params.turn) || params.turn.id !== this.#turnId) return undefined;
    }
    if (message.method === "item/agentMessage/delta" && typeof params.delta === "string") {
      this.parts.push(params.delta);
      return params.delta;
    }
    if (message.method === "item/completed" && isRecord(params.item)
      && params.item.type === "agentMessage" && typeof params.item.text === "string") {
      this.#finalText = params.item.text;
    }
    if (message.method === "thread/tokenUsage/updated" && isRecord(params.tokenUsage) && isRecord(params.tokenUsage.last)) {
      this.usage = tokenUsageFromCodex(params.tokenUsage.last);
    }
    if (message.method === "model/rerouted") {
      const destination = params.toModel ?? params.model ?? params.targetModel;
      if (typeof destination === "string") this.model = destination;
    }
    if (message.method === "error") {
      this.#error = isRecord(params.error) && typeof params.error.message === "string"
        ? params.error.message
        : typeof params.message === "string" ? params.message : "Codex 返回错误";
    }
    if (message.method === "turn/completed" && isRecord(params.turn) && params.turn.id === this.#turnId) {
      this.#turnStatus = String(params.turn.status ?? "");
      if (isRecord(params.turn.error) && typeof params.turn.error.message === "string") this.#error = params.turn.error.message;
      this.completed = true;
    }
    return undefined;
  }

  complete(): { content: string; usage: TokenUsage; model: string } {
    if (!this.completed) throw new PurchaseInvalidResponse("缺少 turn/completed 事件");
    if (this.#turnStatus === "interrupted") throw new PurchaseProviderInterrupted("用户已中止模型请求");
    if (this.#turnStatus !== "completed") throw new PurchaseProviderError(this.#error ?? "模型请求未成功完成");
    const content = this.#finalText ?? this.parts.join("");
    if (!content.trim()) throw new PurchaseInvalidResponse("模型已完成，但没有返回有效文字");
    return { content, usage: this.usage, model: this.model };
  }
}

export interface CodexAppServerAdapterOptions {
  readonly cwd: string;
  readonly transport?: CodexAppServerTransport;
  readonly approvalPrompt?: (command: string, reason: string) => Promise<"accept" | "decline">;
}

export class CodexAppServerProviderAdapter {
  providerRuntime: ProviderRuntime;
  actualModel: string;
  threadId: string | undefined;
  activeTurnId: string | undefined;
  readonly transport: CodexAppServerTransport;
  readonly #cwd: string;
  readonly #approvalPrompt: (command: string, reason: string) => Promise<"accept" | "decline">;

  constructor(providerRuntime: ProviderRuntime, config: PurchaseConfig, options: CodexAppServerAdapterOptions) {
    if (!providerRuntime.codexPath) throw new PurchaseProviderError("Codex Provider 缺少 codex 命令路径");
    this.providerRuntime = providerRuntime;
    this.actualModel = providerRuntime.model;
    this.#cwd = options.cwd;
    this.#approvalPrompt = options.approvalPrompt ?? defaultApprovalPrompt;
    this.transport = options.transport ?? new CodexAppServerTransport({
      command: providerRuntime.codexPath,
      args: ["app-server"],
      timeoutSeconds: config.requestTimeoutSeconds,
    });
    this.transport.setServerRequestHandler((request) => this.#handleServerRequest(request));
  }

  openSession(session: PurchaseSession, _history: readonly Message[]): string {
    this.threadId = undefined;
    return `codex_pending_${session.id}`;
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
    const threadId = await this.#ensureThread();
    try {
      const result = await this.transport.request("turn/start", buildCodexTurnRequest(threadId, options.userInput));
      const turnId = isRecord(result.turn) && typeof result.turn.id === "string" ? result.turn.id : undefined;
      if (!turnId) throw new PurchaseInvalidResponse("Codex 未返回有效 turn id");
      this.activeTurnId = turnId;
      const collector = new CodexStreamCollector(threadId, turnId, this.actualModel);
      options.onStreamStarted();
      while (!collector.completed) {
        const delta = collector.consume(await this.transport.nextMessage());
        if (delta) options.onDelta(delta);
      }
      const completed = collector.complete();
      if (collector.parts.length === 0 && completed.content) options.onDelta(completed.content);
      this.actualModel = completed.model;
      return { content: completed.content, usage: completed.usage, actualModel: completed.model, providerThreadId: threadId };
    } catch (error) {
      if (error instanceof PurchaseInvalidResponse) await this.interrupt();
      throw error;
    } finally {
      this.activeTurnId = undefined;
    }
  }

  async interrupt(): Promise<void> {
    if (!this.threadId || !this.activeTurnId) return;
    try { await this.transport.request("turn/interrupt", { threadId: this.threadId, turnId: this.activeTurnId }, 15); }
    catch { /* best-effort interruption */ }
  }

  async close(): Promise<void> {
    await this.transport.close();
  }

  async #ensureThread(): Promise<string> {
    if (this.threadId) return this.threadId;
    await this.transport.start();
    const result = await this.transport.request("thread/start", { cwd: this.#cwd });
    const thread = isRecord(result.thread) ? result.thread : undefined;
    const threadId = (thread?.id ?? thread?.sessionId ?? result.sessionId ?? result.threadId);
    if (typeof threadId !== "string" || !threadId) throw new PurchaseInvalidResponse("Codex 未返回有效 thread id");
    if (typeof result.model === "string" && result.model) this.actualModel = result.model;
    this.threadId = threadId;
    return threadId;
  }

  async #handleServerRequest(request: JsonObject): Promise<void> {
    const method = request.method;
    const params = isRecord(request.params) ? request.params : {};
    if (method === "item/commandExecution/requestApproval") {
      const decision = await this.#approvalPrompt(String(params.command ?? ""), String(params.reason ?? "Codex 请求执行命令"));
      this.transport.respond(request.id, { decision });
    } else if (method === "item/fileChange/requestApproval") {
      const decision = await this.#approvalPrompt("apply_patch", String(params.reason ?? "Codex 请求修改文件"));
      this.transport.respond(request.id, { decision });
    } else if (method === "item/permissions/requestApproval") {
      this.transport.respond(request.id, { decision: "decline" });
    } else if (method === "mcpServer/elicitation/request") {
      this.transport.respond(request.id, {
        action: params.serverName === "1688-tools" ? "accept" : "decline",
        content: null,
        _meta: null,
      });
    } else {
      this.transport.respondError(request.id, -32601, `不支持的 Codex Server 请求：${String(method)}`);
    }
  }
}

class AsyncMessageQueue {
  readonly #messages: JsonObject[] = [];
  readonly #waiters: Array<{ resolve: (value: JsonObject) => void; reject: (error: Error) => void; timer: NodeJS.Timeout }> = [];
  #ended: Error | undefined;

  push(message: JsonObject): void {
    const waiter = this.#waiters.shift();
    if (waiter) {
      clearTimeout(waiter.timer);
      waiter.resolve(message);
    } else this.#messages.push(message);
  }

  async next(timeoutMs: number): Promise<JsonObject> {
    const message = this.#messages.shift();
    if (message) return message;
    if (this.#ended) throw this.#ended;
    return await new Promise<JsonObject>((resolvePromise, rejectPromise) => {
      const timer = setTimeout(() => {
        const index = this.#waiters.findIndex((item) => item.resolve === resolvePromise);
        if (index >= 0) this.#waiters.splice(index, 1);
        rejectPromise(new PurchaseProviderError("等待 Codex 流式回复超时"));
      }, timeoutMs);
      timer.unref();
      this.#waiters.push({ resolve: resolvePromise, reject: rejectPromise, timer });
    });
  }

  end(error: Error): void {
    this.#ended = error;
    for (const waiter of this.#waiters.splice(0)) {
      clearTimeout(waiter.timer);
      waiter.reject(error);
    }
  }
}

async function defaultApprovalPrompt(command: string, reason: string): Promise<"accept" | "decline"> {
  process.stdout.write(`\nCodex 请求授权：${reason}\n${command ? `${command}\n` : ""}`);
  const prompt = createPrompts({ input: process.stdin, output: process.stdout });
  try {
    const answer = (await prompt.question("允许本次操作？[y/N] ")).trim().toLowerCase();
    return ["y", "yes", "是"].includes(answer) ? "accept" : "decline";
  } catch {
    return "decline";
  } finally {
    prompt.close();
  }
}

async function settleWithin(promise: Promise<void>, milliseconds: number): Promise<boolean> {
  let timer: NodeJS.Timeout | undefined;
  const timeout = new Promise<false>((resolvePromise) => {
    timer = setTimeout(() => resolvePromise(false), milliseconds);
    timer.unref();
  });
  const result = await Promise.race([promise.then(() => true), timeout]);
  if (timer) clearTimeout(timer);
  return result;
}

function isRecord(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
