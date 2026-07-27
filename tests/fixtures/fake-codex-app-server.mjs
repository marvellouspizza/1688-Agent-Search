import { createInterface } from "node:readline";
import { appendFileSync } from "node:fs";

const lines = createInterface({ input: process.stdin });
let activeThreadId = "thread_1";
for await (const line of lines) {
  const message = JSON.parse(line);
  if (process.env.AS1688_TEST_RPC_LOG && message.method) {
    appendFileSync(process.env.AS1688_TEST_RPC_LOG, `${JSON.stringify({ method: message.method, params: message.params })}\n`);
  }
  if (message.method === "initialize") {
    process.stdout.write(`${JSON.stringify({ id: message.id, result: {} })}\n`);
  } else if (message.method === "thread/start") {
    activeThreadId = "thread_1";
    process.stdout.write(`${JSON.stringify({ id: message.id, result: { thread: { id: activeThreadId }, model: message.params.model } })}\n`);
  } else if (message.method === "thread/resume") {
    activeThreadId = message.params.threadId;
    process.stdout.write(`${JSON.stringify({ id: message.id, result: { thread: { id: activeThreadId }, model: message.params.model } })}\n`);
  } else if (message.method === "turn/start") {
    process.stdout.write(`${JSON.stringify({ id: message.id, result: { turn: { id: "turn_1" } } })}\n`);
    process.stdout.write(`${JSON.stringify({ method: "item/agentMessage/delta", params: { threadId: activeThreadId, turnId: "turn_1", delta: "答" } })}\n`);
    process.stdout.write(`${JSON.stringify({ method: "item/agentMessage/delta", params: { threadId: activeThreadId, turnId: "turn_1", delta: "案" } })}\n`);
    process.stdout.write(`${JSON.stringify({ method: "thread/tokenUsage/updated", params: { threadId: activeThreadId, turnId: "turn_1", tokenUsage: { last: { inputTokens: 2, outputTokens: 1, totalTokens: 3 } } } })}\n`);
    process.stdout.write(`${JSON.stringify({ method: "turn/completed", params: { threadId: activeThreadId, turnId: "turn_1", turn: { id: "turn_1", status: "completed" } } })}\n`);
  } else if (message.method === "turn/interrupt") {
    process.stdout.write(`${JSON.stringify({ id: message.id, result: {} })}\n`);
  }
}
