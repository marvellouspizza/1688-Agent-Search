import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import type { ProviderRuntime, ProviderTurnResult, PurchaseSession } from "../dist/models.js";
import { PurchaseAgentRuntime, type PurchaseProviderAdapter } from "../dist/runtime.js";
import { PurchaseSessionStore } from "../dist/session-store.js";
import { ToolRegistry } from "../dist/tools/registry.js";

const providerRuntime: ProviderRuntime = {
  provider: "local-codex-chatgpt",
  model: "gpt-5.6-sol",
  apiMode: "codex_responses",
  baseUrl: "https://chatgpt.com/backend-api/codex",
  credentialSource: "test",
};

const baseConfig = {
  openaiRuntime: "auto" as const,
  requestTimeoutSeconds: 3,
  maxContextCharacters: 120_000,
  searxngBaseUrl: "http://127.0.0.1:8888",
  searxngTimeoutSeconds: 3,
  maxIterations: 5,
};

class ScriptedProvider implements PurchaseProviderAdapter {
  providerRuntime = providerRuntime;
  actualModel = providerRuntime.model;
  threadId: string | undefined;
  readonly turns: Array<{ inputItems: readonly Record<string, unknown>[]; toolDefinitions: readonly Record<string, unknown>[] }> = [];
  readonly #script: ProviderTurnResult[];

  constructor(script: ProviderTurnResult[]) { this.#script = [...script]; }
  openSession(session: PurchaseSession): string { this.threadId = `resp_${session.id}`; return this.threadId; }
  switchModel(model: string): void { this.providerRuntime = { ...this.providerRuntime, model }; this.actualModel = model; }
  interrupt(): void {}
  close(): void {}
  async runModelTurn(options: {
    inputItems: readonly Record<string, unknown>[];
    toolDefinitions: readonly Record<string, unknown>[];
    onStreamStarted: () => void;
    onDelta: (delta: string) => void;
  }): Promise<ProviderTurnResult> {
    this.turns.push({ inputItems: structuredClone(options.inputItems), toolDefinitions: structuredClone(options.toolDefinitions) });
    options.onStreamStarted();
    const result = this.#script.shift();
    if (!result) throw new Error("script exhausted");
    if (result.content) options.onDelta(result.content);
    return result;
  }
}

function turn(overrides: Partial<ProviderTurnResult>): ProviderTurnResult {
  return {
    content: "",
    toolCalls: [],
    responseItems: [],
    usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 },
    actualModel: providerRuntime.model,
    responseId: "resp_1",
    providerThreadId: "resp_1",
    ...overrides,
  };
}

function createRuntime(provider: ScriptedProvider, registry = new ToolRegistry(), maxIterations = 5): PurchaseAgentRuntime {
  const store = new PurchaseSessionStore(join(mkdtempSync(join(tmpdir(), "as1688-runtime-")), "sessions.db"));
  return new PurchaseAgentRuntime({
    config: { ...baseConfig, maxIterations },
    providerRuntime,
    sessionStore: store,
    promptBuilder: { countContextCharacters: () => 1 },
    providerAdapter: provider,
    toolRegistry: registry,
  });
}

test("plain final response completes and returns runtime to idle", async () => {
  const provider = new ScriptedProvider([turn({ content: "done" })]);
  const runtime = createRuntime(provider);
  const result = await runtime.chat("hello");
  assert.equal(result.status, "completed");
  assert.equal(result.content, "done");
  assert.equal(runtime.state, "idle");
  await runtime.close();
});

test("parallel-safe calls dispatch concurrently and replay in call order", async () => {
  const registry = new ToolRegistry();
  let concurrent = 0;
  let maximum = 0;
  const handler = async ({ value }: Record<string, unknown>) => {
    concurrent += 1;
    maximum = Math.max(maximum, concurrent);
    await new Promise((resolve) => setTimeout(resolve, value === 1 ? 20 : 1));
    concurrent -= 1;
    return { value };
  };
  registry.register({ name: "one", description: "one", inputSchema: {}, handler, parallelSafe: true });
  registry.register({ name: "two", description: "two", inputSchema: {}, handler, parallelSafe: true });
  const provider = new ScriptedProvider([
    turn({
      toolCalls: [
        { callId: "call_1", name: "one", arguments: { value: 1 } },
        { callId: "call_2", name: "two", arguments: { value: 2 } },
      ],
      responseItems: [{ type: "function_call", call_id: "call_1" }, { type: "function_call", call_id: "call_2" }],
    }),
    turn({ content: "finished" }),
  ]);
  const runtime = createRuntime(provider, registry);
  const result = await runtime.chat("find");
  assert.equal(result.status, "completed");
  assert.equal(maximum, 2);
  const outputs = provider.turns[1]?.inputItems.filter((item) => item.type === "function_call_output");
  assert.deepEqual(outputs?.map((item) => item.call_id), ["call_1", "call_2"]);
  await runtime.close();
});

test("iteration exhaustion uses a tool-free grace summary", async () => {
  const registry = new ToolRegistry();
  registry.register({ name: "lookup", description: "lookup", inputSchema: {}, handler: () => ({ ok: true }) });
  const provider = new ScriptedProvider([
    turn({ toolCalls: [{ callId: "call_1", name: "lookup", arguments: {} }], responseItems: [{ type: "function_call" }] }),
    turn({ content: "summary" }),
  ]);
  const runtime = createRuntime(provider, registry, 1);
  const result = await runtime.chat("research");
  assert.equal(result.content, "summary");
  assert.deepEqual(provider.turns.at(-1)?.toolDefinitions, []);
  await runtime.close();
});

test("duplicate normalized calls fail before a second dispatch", async () => {
  const registry = new ToolRegistry();
  let dispatched = 0;
  registry.register({ name: "lookup", description: "lookup", inputSchema: {}, handler: () => { dispatched += 1; return {}; } });
  const provider = new ScriptedProvider([
    turn({ toolCalls: [{ callId: "call_1", name: "lookup", arguments: { b: 2, a: 1 } }], responseItems: [] }),
    turn({ toolCalls: [{ callId: "call_2", name: "lookup", arguments: { a: 1, b: 2 } }], responseItems: [] }),
  ]);
  const runtime = createRuntime(provider, registry);
  const result = await runtime.chat("loop");
  assert.equal(result.status, "failed");
  assert.match(result.error ?? "", /重复调用/);
  assert.equal(dispatched, 1);
  await runtime.close();
});
