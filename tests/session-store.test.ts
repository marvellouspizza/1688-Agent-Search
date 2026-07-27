import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { test } from "node:test";

import { PurchaseSessionStore } from "../dist/session-store.js";
import type { ProviderRuntime } from "../dist/models.js";

const providerRuntime: ProviderRuntime = {
  provider: "local-codex-chatgpt",
  model: "gpt-5.6-sol",
  apiMode: "codex_responses",
  baseUrl: "https://chatgpt.com/backend-api/codex",
  credentialSource: "codex-login",
};

function databasePath(): string {
  return join(mkdtempSync(join(tmpdir(), "as1688-store-")), "sessions.db");
}

test("opens a Python-created database without data migration", () => {
  const path = databasePath();
  const script = String.raw`
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.executescript("""
CREATE TABLE sessions (id TEXT PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL, provider_thread_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE messages (id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, role TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE requests (id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, user_message_id TEXT NOT NULL REFERENCES messages(id), status TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, total_tokens INTEGER NOT NULL DEFAULT 0, error TEXT, owner_id TEXT, started_at TEXT NOT NULL, completed_at TEXT);
INSERT INTO sessions VALUES ('session_existing', 'local-codex-chatgpt', 'gpt-5.6-sol', 'thread_existing', '2026-07-27T10:00:00+08:00', '2026-07-27T10:00:02+08:00');
INSERT INTO messages VALUES ('msg_user', 'session_existing', 'user', 'hello', 'completed', 'local-codex-chatgpt', 'gpt-5.6-sol', '2026-07-27T10:00:00+08:00');
INSERT INTO messages VALUES ('msg_assistant', 'session_existing', 'assistant', 'world', 'completed', 'local-codex-chatgpt', 'gpt-5.6-sol', '2026-07-27T10:00:02+08:00');
INSERT INTO requests VALUES ('request_existing', 'session_existing', 'msg_user', 'completed', 'local-codex-chatgpt', 'gpt-5.6-sol', 1, 1, 2, NULL, NULL, '2026-07-27T10:00:00+08:00', '2026-07-27T10:00:02+08:00');
db.commit()
`;
  const created = spawnSync("python3", ["-c", script, path], { encoding: "utf8" });
  assert.equal(created.status, 0, created.stderr);

  const store = new PurchaseSessionStore(path);
  assert.equal(store.getSession("session_existing").providerThreadId, "thread_existing");
  assert.deepEqual(store.loadContextMessages("session_existing").map((item) => item.content), ["hello", "world"]);
  store.close();
});

test("request and reply preserve atomic session and usage semantics", () => {
  const store = new PurchaseSessionStore(databasePath());
  const session = store.createOrRestoreSession(undefined, providerRuntime);
  store.acquireSessionLock(session.id);
  const { userMessage, requestId } = store.beginRequest(session.id, "find bearing", providerRuntime);
  store.markRequestStreaming(requestId);
  store.appendToolTrace({
    sessionId: session.id,
    requestId,
    sequence: 1,
    callId: "call_1",
    name: "web_search",
    argumentsJson: '{"query":"bearing"}',
    resultJson: '{"results":[]}',
    status: "completed",
    durationMs: 2,
  });
  const reply = store.saveReply({
    sessionId: session.id,
    requestId,
    content: "done",
    providerRuntime,
    actualModel: "gpt-5.6-sol",
    usage: { inputTokens: 1, outputTokens: 2, totalTokens: 3 },
    providerThreadId: "thread_1",
  });
  assert.equal(userMessage.status, "completed");
  assert.equal(reply.status, "completed");
  assert.equal(store.getSession(session.id).providerThreadId, "thread_1");
  assert.deepEqual(store.loadContextMessages(session.id).map((item) => item.role), ["user", "assistant"]);
  store.close();
});

test("a session lock rejects a second live owner", () => {
  const path = databasePath();
  const first = new PurchaseSessionStore(path);
  const session = first.createOrRestoreSession(undefined, providerRuntime);
  first.acquireSessionLock(session.id);
  const second = new PurchaseSessionStore(path);
  assert.throws(() => second.acquireSessionLock(session.id), /另一个 CLI/);
  second.close();
  first.close();
});

test("startup recovers requests whose owner lock was released", () => {
  const path = databasePath();
  const first = new PurchaseSessionStore(path);
  const session = first.createOrRestoreSession(undefined, providerRuntime);
  const { requestId, userMessage } = first.beginRequest(session.id, "unfinished", providerRuntime);
  first.markRequestStreaming(requestId);
  first.close();

  const recovered = new PurchaseSessionStore(path);
  assert.deepEqual(recovered.loadContextMessages(session.id), []);
  assert.doesNotThrow(() => recovered.failRequest({
    requestId,
    userMessageId: userMessage.id,
    status: "incomplete",
    error: "already recovered",
  }));
  recovered.close();
});

test("failed requests never enter restored model context", () => {
  const store = new PurchaseSessionStore(databasePath());
  const session = store.createOrRestoreSession(undefined, providerRuntime);
  const { requestId, userMessage } = store.beginRequest(session.id, "bad turn", providerRuntime);
  store.failRequest({ requestId, userMessageId: userMessage.id, status: "failed", error: "boom" });
  assert.deepEqual(store.loadContextMessages(session.id), []);
  assert.equal(store.listSessions(20)[0]?.id, session.id);
  store.close();
});
