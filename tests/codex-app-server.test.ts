import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
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

test("turn request gives app-server only the current user input and selected model", () => {
  assert.deepEqual(buildCodexTurnRequest("thread_1", "hello", "gpt-5.6-sol"), {
    threadId: "thread_1",
    input: [{ type: "text", text: "hello" }],
    model: "gpt-5.6-sol",
  });
});

test("optional app-server starts lazily and completes after turn/completed", async () => {
  const logPath = join(mkdtempSync(join(tmpdir(), "as1688-app-server-")), "rpc.jsonl");
  const transport = new CodexAppServerTransport({
    command: process.execPath,
    args: [fixture],
    timeoutSeconds: 3,
    environment: { ...process.env, AS1688_TEST_RPC_LOG: logPath },
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
  const requests = readRpcLog(logPath);
  assert.deepEqual(requests.find((item) => item.method === "thread/start")?.params, {
    cwd: process.cwd(), model: runtime.model,
  });
  assert.equal(requests.find((item) => item.method === "turn/start")?.params.model, runtime.model);
});

test("restored app-server sessions resume their Codex thread", async () => {
  const logPath = join(mkdtempSync(join(tmpdir(), "as1688-app-server-resume-")), "rpc.jsonl");
  const transport = new CodexAppServerTransport({
    command: process.execPath,
    args: [fixture],
    timeoutSeconds: 3,
    environment: { ...process.env, AS1688_TEST_RPC_LOG: logPath },
  });
  const adapter = new CodexAppServerProviderAdapter(runtime, {
    openaiRuntime: "codex_app_server",
    requestTimeoutSeconds: 3,
    maxContextCharacters: 120_000,
    searxngBaseUrl: "http://127.0.0.1:8888",
    searxngTimeoutSeconds: 3,
    maxIterations: 500,
  }, { cwd: process.cwd(), transport });
  assert.equal(adapter.openSession({ ...session, providerThreadId: "thread_existing" }, []), "thread_existing");
  const result = await adapter.streamModelReply({
    userInput: "continue",
    userMessageId: "msg_2",
    onStreamStarted: () => {},
    onDelta: () => {},
  });
  assert.equal(result.providerThreadId, "thread_existing");
  await adapter.close();
  const requests = readRpcLog(logPath);
  assert.equal(requests.some((item) => item.method === "thread/start"), false);
  assert.deepEqual(requests.find((item) => item.method === "thread/resume")?.params, {
    threadId: "thread_existing", cwd: process.cwd(), model: runtime.model,
  });
});

function readRpcLog(path: string): Array<{ method: string; params: Record<string, unknown> }> {
  return readFileSync(path, "utf8").trim().split("\n").map((line) => JSON.parse(line));
}
