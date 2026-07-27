import { createInterface } from "node:readline";

import { resolveSkillRoot } from "../config.js";
import { ToolRegistry } from "./registry.js";
import { buildToolRegistry } from "./web/search.js";

export const MCP_PROTOCOL_VERSION = "2024-11-05";

export async function handleMcpMessage(
  message: Record<string, unknown>,
  registry: ToolRegistry,
): Promise<Record<string, unknown> | undefined> {
  const method = message.method;
  const id = message.id;
  if (method === "notifications/initialized") return undefined;
  if (method === "initialize") {
    return response(id, {
      protocolVersion: MCP_PROTOCOL_VERSION,
      capabilities: { tools: {} },
      serverInfo: { name: "1688-tools", version: "1.0.0" },
    });
  }
  if (method === "tools/list") return response(id, { tools: registry.definitions() });
  if (method === "tools/call") {
    if (!isRecord(message.params)) return rpcError(id, -32602, "tools/call 参数无效");
    const name = message.params.name;
    const arguments_ = message.params.arguments ?? {};
    if (typeof name !== "string" || !isRecord(arguments_)) return rpcError(id, -32602, "工具名称或参数无效");
    try {
      const result = await registry.dispatch(name, arguments_);
      return response(id, { content: [{ type: "text", text: JSON.stringify(result) }] });
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      return response(id, {
        content: [{ type: "text", text: detail.slice(0, 1_000) }],
        isError: true,
      });
    }
  }
  return rpcError(id, -32601, `不支持的 MCP 方法：${String(method)}`);
}

export async function runMcpServer(options: {
  readonly input?: NodeJS.ReadableStream;
  readonly output?: { write(chunk: string): unknown };
  readonly cwd?: string;
} = {}): Promise<number> {
  const input = options.input ?? process.stdin;
  const output = options.output ?? process.stdout;
  const registry = buildToolRegistry({ skillRoot: resolveSkillRoot(options.cwd ?? process.cwd()) });
  try {
    const lines = createInterface({ input });
    for await (const line of lines) {
      let message: unknown;
      try { message = JSON.parse(line); } catch { continue; }
      if (!isRecord(message)) continue;
      const result = await handleMcpMessage(message, registry);
      if (result) output.write(`${JSON.stringify(result)}\n`);
    }
    return 0;
  } finally {
    await registry.close();
  }
}

function response(id: unknown, result: Record<string, unknown>): Record<string, unknown> {
  return { jsonrpc: "2.0", id, result };
}

function rpcError(id: unknown, code: number, message: string): Record<string, unknown> {
  return { jsonrpc: "2.0", id, error: { code, message } };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
