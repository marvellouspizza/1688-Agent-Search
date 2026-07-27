import assert from "node:assert/strict";
import { test } from "node:test";

import {
  EMPTY_USAGE,
  tokenUsageFromCodex,
  validateConversationRoles,
  type Message,
} from "../src/models.ts";

function message(role: Message["role"], id: string): Message {
  return {
    id,
    sessionId: "session_1",
    role,
    content: id,
    status: "completed",
    provider: "local-codex-chatgpt",
    model: "gpt-5.6-sol",
    createdAt: "2026-07-27T12:00:00+08:00",
  };
}

test("completed conversation messages alternate user and assistant", () => {
  assert.doesNotThrow(() => validateConversationRoles([
    message("user", "msg_1"),
    message("assistant", "msg_2"),
  ]));
  assert.throws(
    () => validateConversationRoles([message("assistant", "msg_1")]),
    /期望 user，实际 assistant/,
  );
  assert.throws(
    () => validateConversationRoles([message("user", "msg_1")]),
    /尚未配对/,
  );
});

test("incomplete messages are ignored during role validation", () => {
  const incomplete = { ...message("assistant", "msg_2"), status: "failed" as const };
  assert.throws(
    () => validateConversationRoles([message("user", "msg_1"), incomplete]),
    /尚未配对/,
  );
});

test("Codex usage accepts absent and numeric-like values", () => {
  assert.deepEqual(tokenUsageFromCodex(undefined), EMPTY_USAGE);
  assert.deepEqual(
    tokenUsageFromCodex({ inputTokens: "2", outputTokens: 3, totalTokens: 5 }),
    { inputTokens: 2, outputTokens: 3, totalTokens: 5 },
  );
});
