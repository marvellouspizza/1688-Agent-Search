import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProviderRuntime, PurchaseSession } from "../dist/models.js";
import {
  OpenAIResponsesProviderAdapter,
  iterateSseEvents,
  listOpenAiModels,
} from "../dist/providers/openai.js";
import { PurchaseProviderError, PurchaseProviderInterrupted } from "../dist/providers/errors.js";

const runtime: ProviderRuntime = {
  provider: "openai-api",
  model: "gpt-5.6",
  apiMode: "openai_responses_sse",
  baseUrl: "https://api.openai.test/v1",
  credentialSource: "test",
  credential: "sk-test",
};

const session: PurchaseSession = {
  id: "session_1",
  provider: "openai-api",
  model: "gpt-5.6",
  createdAt: "2026-07-27T00:00:00+08:00",
  updatedAt: "2026-07-27T00:00:00+08:00",
};

function sse(events: readonly Record<string, unknown>[]): Response {
  const body = `${events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("")}data: [DONE]\n\n`;
  return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
}

test("SSE parser preserves split multi-line events", async () => {
  const response = new Response("data: {\"type\":\ndata: \"response.created\"}\n\ndata: [DONE]\n\n");
  const events: Record<string, unknown>[] = [];
  for await (const event of iterateSseEvents(response)) events.push(event);
  assert.deepEqual(events, [{ type: "response.created" }]);
});

test("OpenAI model catalog filters non-text models", async () => {
  const models = await listOpenAiModels(runtime, {
    fetchImpl: async () => new Response(JSON.stringify({ data: [
      { id: "gpt-5.6", owned_by: "openai" },
      { id: "gpt-image-1", owned_by: "openai" },
      { id: "text-embedding-3-small", owned_by: "openai" },
    ] }), { status: 200 }),
  });
  assert.deepEqual(models.map((item) => item.model), ["gpt-5.6"]);
});

test("OpenAI adapter streams text and stores local history", async () => {
  const deltas: string[] = [];
  let started = 0;
  const adapter = new OpenAIResponsesProviderAdapter(runtime, {
    openaiRuntime: "auto",
    requestTimeoutSeconds: 3,
    maxContextCharacters: 120_000,
    searxngBaseUrl: "http://127.0.0.1:8888",
    searxngTimeoutSeconds: 3,
    maxIterations: 500,
  }, {
    buildBaseInstructions: () => "system",
    buildContext: () => "",
  }, {
    fetchImpl: async () => sse([
      { type: "response.created" },
      { type: "response.output_item.added", item: { type: "message" } },
      { type: "response.output_text.delta", delta: "答" },
      { type: "response.output_text.delta", delta: "案" },
      { type: "response.output_item.done", item: { type: "message" } },
      { type: "response.completed", response: { status: "completed", model: "gpt-5.6", usage: { input_tokens: 2, output_tokens: 1, total_tokens: 3 } } },
    ]),
  });
  assert.equal(adapter.openSession(session, []), "openai_local_session_1");
  const result = await adapter.streamModelReply({
    userInput: "问题",
    userMessageId: "msg_1",
    onStreamStarted: () => { started += 1; },
    onDelta: (delta) => deltas.push(delta),
  });
  assert.equal(started, 1);
  assert.deepEqual(deltas, ["答", "案"]);
  assert.equal(result.content, "答案");
  assert.equal(result.usage.totalTokens, 3);
});

test("OpenAI request timeout is a provider failure, not a user interruption", async () => {
  const adapter = new OpenAIResponsesProviderAdapter(runtime, {
    openaiRuntime: "auto",
    requestTimeoutSeconds: 0.01,
    maxContextCharacters: 120_000,
    searxngBaseUrl: "http://127.0.0.1:8888",
    searxngTimeoutSeconds: 3,
    maxIterations: 500,
  }, { buildBaseInstructions: () => "system", buildContext: () => "" }, {
    fetchImpl: async (_input, init) => await abortedFetch(init),
  });
  adapter.openSession(session, []);
  await assert.rejects(
    adapter.streamModelReply({ userInput: "wait", userMessageId: "msg_2", onStreamStarted: () => {}, onDelta: () => {} }),
    (error: unknown) => error instanceof PurchaseProviderError
      && !(error instanceof PurchaseProviderInterrupted) && /超时/.test(error.message),
  );
});

test("OpenAI streaming timeout is a provider failure, not a user interruption", async () => {
  const adapter = new OpenAIResponsesProviderAdapter(runtime, {
    openaiRuntime: "auto",
    requestTimeoutSeconds: 0.01,
    maxContextCharacters: 120_000,
    searxngBaseUrl: "http://127.0.0.1:8888",
    searxngTimeoutSeconds: 3,
    maxIterations: 500,
  }, { buildBaseInstructions: () => "system", buildContext: () => "" }, {
    fetchImpl: async (_input, init) => hangingSseResponse(init),
  });
  adapter.openSession(session, []);
  await assert.rejects(
    adapter.streamModelReply({ userInput: "wait", userMessageId: "msg_3", onStreamStarted: () => {}, onDelta: () => {} }),
    (error: unknown) => error instanceof PurchaseProviderError
      && !(error instanceof PurchaseProviderInterrupted) && /超时/.test(error.message),
  );
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
