import assert from "node:assert/strict";
import { test } from "node:test";

import { handleMcpMessage } from "../dist/tools/mcp-server.js";
import { ToolRegistry } from "../dist/tools/registry.js";

test("MCP initialize, list, and call use JSON-RPC 2.0", async () => {
  const registry = new ToolRegistry();
  registry.register({ name: "sample", description: "sample", inputSchema: { type: "object" }, handler: ({ value }) => ({ value }) });
  const initialized = await handleMcpMessage({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} }, registry);
  assert.equal((initialized?.result as Record<string, unknown>).protocolVersion, "2024-11-05");
  const listed = await handleMcpMessage({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} }, registry);
  assert.equal(((listed?.result as Record<string, unknown>).tools as unknown[]).length, 1);
  const called = await handleMcpMessage({ jsonrpc: "2.0", id: 3, method: "tools/call", params: { name: "sample", arguments: { value: 2 } } }, registry);
  assert.equal(JSON.parse((((called?.result as Record<string, unknown>).content as Array<Record<string, string>>)[0]?.text ?? "{}"))?.value, 2);
});

test("MCP returns protocol errors and tool errors without crashing", async () => {
  const registry = new ToolRegistry();
  const invalid = await handleMcpMessage({ id: 1, method: "tools/call", params: null }, registry);
  assert.equal((invalid?.error as Record<string, unknown>).code, -32602);
  const missing = await handleMcpMessage({ id: 2, method: "tools/call", params: { name: "missing", arguments: {} } }, registry);
  assert.equal((missing?.result as Record<string, unknown>).isError, true);
});
