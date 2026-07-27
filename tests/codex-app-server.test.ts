import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

import type { ProviderRuntime, PurchaseSession } from "../dist/models.js";
import {
  CodexAppServerProviderAdapter,
  CodexAppServerTransport,
  buildCodexTurnRequest,
} from "../dist/providers/codex-app-server.js";

const fixture = fileURLToPath(new URL("./fixtures/fake-codex-app-server.mjs", import.meta.url));
const runtime: ProviderRuntime = {
  provider: "local-codex-chatgpt",
  model: "gpt-5.6-sol",
  apiMode: "codex_app_server",
  baseUrl: "https://chatgpt.com/backend-api/codex",
  credentialSource: "codex-cli-chatgpt-oauth",
  codexPath: process.execPath,
};
const session: PurchaseSession = {
  id: "session_1",
  provider: runtime.provider,
  model: runtime.model,
  createdAt: "2026-07-27T00:00:00+08:00",
  updatedAt: "2026-07-27T00:00:00+08:00",
};

test("turn request gives app-server only the current user input", () => {
  assert.deepEqual(buildCodexTurnRequest("thread_1", "hello"), {
    threadId: "thread_1",
    input: [{ type: "text", text: "hello" }],
  });
});

test("optional app-server starts lazily and completes after turn/completed", async () => {
  const transport = new CodexAppServerTransport({
    command: process.execPath,
    args: [fixture],
    timeoutSeconds: 3,
  });
  const adapter = new CodexAppServerProviderAdapter(runtime, {
    openaiRuntime: "codex_app_server",
    requestTimeoutSeconds: 3,
    maxContextCharacters: 120_000,
    searxngBaseUrl: "http://127.0.0.1:8888",
    searxngTimeoutSeconds: 3,
    maxIterations: 500,
  }, { cwd: process.cwd(), transport });
  assert.equal(adapter.openSession(session, []), "codex_pending_session_1");
  assert.equal(transport.started, false);
  const deltas: string[] = [];
  const result = await adapter.streamModelReply({
    userInput: "question",
    userMessageId: "msg_1",
    onStreamStarted: () => {},
    onDelta: (delta) => deltas.push(delta),
  });
  assert.equal(transport.started, true);
  assert.deepEqual(deltas, ["答", "案"]);
  assert.equal(result.content, "答案");
  assert.equal(result.usage.totalTokens, 3);
  await adapter.close();
});
