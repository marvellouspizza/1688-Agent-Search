import { createInterface } from "node:readline";

const lines = createInterface({ input: process.stdin });
for await (const line of lines) {
  const message = JSON.parse(line);
  if (message.method === "initialize") {
    process.stdout.write(`${JSON.stringify({ id: message.id, result: {} })}\n`);
  } else if (message.method === "thread/start") {
    process.stdout.write(`${JSON.stringify({ id: message.id, result: { thread: { id: "thread_1" }, model: "gpt-5.6-sol" } })}\n`);
  } else if (message.method === "turn/start") {
    process.stdout.write(`${JSON.stringify({ id: message.id, result: { turn: { id: "turn_1" } } })}\n`);
    process.stdout.write(`${JSON.stringify({ method: "item/agentMessage/delta", params: { threadId: "thread_1", turnId: "turn_1", delta: "答" } })}\n`);
    process.stdout.write(`${JSON.stringify({ method: "item/agentMessage/delta", params: { threadId: "thread_1", turnId: "turn_1", delta: "案" } })}\n`);
    process.stdout.write(`${JSON.stringify({ method: "thread/tokenUsage/updated", params: { threadId: "thread_1", turnId: "turn_1", tokenUsage: { last: { inputTokens: 2, outputTokens: 1, totalTokens: 3 } } } })}\n`);
    process.stdout.write(`${JSON.stringify({ method: "turn/completed", params: { threadId: "thread_1", turnId: "turn_1", turn: { id: "turn_1", status: "completed" } } })}\n`);
  } else if (message.method === "turn/interrupt") {
    process.stdout.write(`${JSON.stringify({ id: message.id, result: {} })}\n`);
  }
}
