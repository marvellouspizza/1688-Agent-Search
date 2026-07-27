import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import type { ProviderRuntime, PurchaseSession } from "../dist/models.js";
import { buildCodexChatGptHeaders, refreshLocalCodexAuth } from "../dist/providers/codex-auth.js";
import { CodexResponsesProviderAdapter } from "../dist/providers/codex-responses.js";
import { PurchaseProviderError, PurchaseProviderInterrupted } from "../dist/providers/errors.js";

const runtime: ProviderRuntime = {
  provider: "local-codex-chatgpt",
  model: "gpt-5.6-sol",
  apiMode: "codex_responses",
  baseUrl: "https://chatgpt.com/backend-api/codex",
  credentialSource: "codex-cli-chatgpt-oauth",
  codexPath: "/usr/local/bin/codex",
};

const session: PurchaseSession = {
  id: "session_1",
  provider: "local-codex-chatgpt",
  model: "gpt-5.6-sol",
  createdAt: "2026-07-27T00:00:00+08:00",
  updatedAt: "2026-07-27T00:00:00+08:00",
};

const config = {
  openaiRuntime: "auto" as const,
  requestTimeoutSeconds: 3,
  maxContextCharacters: 120_000,
  searxngBaseUrl: "http://127.0.0.1:8888",
  searxngTimeoutSeconds: 3,
  maxIterations: 500,
};

function sse(events: readonly Record<string, unknown>[]): Response {
  return new Response(`${events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("")}data: [DONE]\n\n`, { status: 200 });
}

test("Codex headers include account id from OAuth JWT", () => {
  const claims = Buffer.from(JSON.stringify({ "https://api.openai.com/auth": { chatgpt_account_id: "acct_1" } })).toString("base64url");
  const headers = buildCodexChatGptHeaders(`x.${claims}.y`);
  assert.equal(headers["ChatGPT-Account-ID"], "acct_1");
  assert.equal(headers.originator, "codex_cli_rs");
});

test("Codex Responses assembles function calls and response items", async () => {
  const adapter = new CodexResponsesProviderAdapter(runtime, config, {
    buildBaseInstructions: () => "system",
    buildContext: () => "",
  }, {
    fetchImpl: async () => sse([
      { type: "response.output_item.done", item: { type: "function_call", call_id: "call_1", name: "web_search", arguments: '{"query":"轴承"}' } },
      { type: "response.completed", response: { id: "resp_1", model: "gpt-5.6-sol", status: "completed", usage: { input_tokens: 10, output_tokens: 5, total_tokens: 15 } } },
    ]),
    auth: {
      load: () => ({ accessToken: "token", refreshToken: "refresh" }),
      refresh: async () => ({ accessToken: "new", refreshToken: "refresh" }),
      headers: () => ({ Authorization: "Bearer token" }),
    },
  });
  adapter.openSession(session, []);
  const result = await adapter.runModelTurn({
    inputItems: [{ role: "user", content: "find" }],
    toolDefinitions: [{ name: "web_search", description: "search", inputSchema: { type: "object" } }],
    onStreamStarted: () => {},
    onDelta: () => {},
  });
  assert.deepEqual(result.toolCalls, [{ callId: "call_1", name: "web_search", arguments: { query: "轴承" } }]);
  assert.equal(result.responseItems[0]?.type, "function_call");
  assert.equal(result.providerThreadId, "resp_1");
  assert.equal(result.usage.totalTokens, 15);
});

test("Codex Responses refreshes once after a 401", async () => {
  let requests = 0;
  let refreshes = 0;
  const adapter = new CodexResponsesProviderAdapter(runtime, config, {
    buildBaseInstructions: () => "system",
    buildContext: () => "",
  }, {
    fetchImpl: async () => {
      requests += 1;
      if (requests === 1) return new Response("unauthorized", { status: 401 });
      return sse([
        { type: "response.output_item.done", item: { type: "message", role: "assistant", content: [{ type: "output_text", text: "done" }] } },
        { type: "response.completed", response: { id: "resp_2", status: "completed", model: "gpt-5.6-sol", usage: {} } },
      ]);
    },
    auth: {
      load: () => ({ accessToken: "same", refreshToken: "refresh" }),
      refresh: async () => { refreshes += 1; return { accessToken: "new", refreshToken: "refresh" }; },
      headers: (token) => ({ Authorization: `Bearer ${token}` }),
    },
  });
  adapter.openSession(session, []);
  const result = await adapter.runModelTurn({ inputItems: [], toolDefinitions: [], onStreamStarted: () => {}, onDelta: () => {} });
  assert.equal(result.content, "done");
  assert.equal(requests, 2);
  assert.equal(refreshes, 1);
});

test("Codex Responses timeout is a provider failure, not a user interruption", async () => {
  const adapter = new CodexResponsesProviderAdapter(runtime, { ...config, requestTimeoutSeconds: 0.01 }, {
    buildBaseInstructions: () => "system",
    buildContext: () => "",
  }, {
    fetchImpl: async (_input, init) => await abortedFetch(init),
    auth: {
      load: () => ({ accessToken: "token", refreshToken: "refresh" }),
      refresh: async () => ({ accessToken: "new", refreshToken: "refresh" }),
      headers: () => ({ Authorization: "Bearer token" }),
    },
  });
  adapter.openSession(session, []);
  await assert.rejects(
    adapter.runModelTurn({ inputItems: [], toolDefinitions: [], onStreamStarted: () => {}, onDelta: () => {} }),
    (error: unknown) => error instanceof PurchaseProviderError
      && !(error instanceof PurchaseProviderInterrupted) && /超时/.test(error.message),
  );
});

test("Codex Responses streaming timeout is a provider failure, not a user interruption", async () => {
  const adapter = new CodexResponsesProviderAdapter(runtime, { ...config, requestTimeoutSeconds: 0.01 }, {
    buildBaseInstructions: () => "system",
    buildContext: () => "",
  }, {
    fetchImpl: async (_input, init) => hangingSseResponse(init),
    auth: {
      load: () => ({ accessToken: "token", refreshToken: "refresh" }),
      refresh: async () => ({ accessToken: "new", refreshToken: "refresh" }),
      headers: () => ({ Authorization: "Bearer token" }),
    },
  });
  adapter.openSession(session, []);
  await assert.rejects(
    adapter.runModelTurn({ inputItems: [], toolDefinitions: [], onStreamStarted: () => {}, onDelta: () => {} }),
    (error: unknown) => error instanceof PurchaseProviderError
      && !(error instanceof PurchaseProviderInterrupted) && /超时/.test(error.message),
  );
});

test("Codex auth refresh preserves the ISO timestamp schema", async () => {
  const codexHome = mkdtempSync(join(tmpdir(), "as1688-codex-auth-"));
  const authPath = join(codexHome, "auth.json");
  writeFileSync(authPath, JSON.stringify({
    auth_mode: "chatgpt",
    tokens: { access_token: "old", refresh_token: "refresh" },
    last_refresh: "2026-07-01T00:00:00Z",
  }), { mode: 0o600 });
  await refreshLocalCodexAuth({
    codexHome,
    fetchImpl: async () => new Response(JSON.stringify({ access_token: "new" }), { status: 200 }),
  });
  const stored = JSON.parse(readFileSync(authPath, "utf8"));
  assert.equal(typeof stored.last_refresh, "string");
  assert.match(stored.last_refresh, /^\d{4}-\d{2}-\d{2}T.*Z$/);
});

function abortedFetch(init?: RequestInit): Promise<Response> {
  return new Promise((_resolve, reject) => {
    const signal = init?.signal;
    if (!signal) return reject(new Error("missing signal"));
    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
  });
}

function hangingSseResponse(init?: RequestInit): Response {
  const signal = init?.signal;
  return new Response(new ReadableStream({
    start(controller) {
      signal?.addEventListener("abort", () => controller.error(new DOMException("aborted", "AbortError")), { once: true });
    },
  }), { status: 200 });
}
