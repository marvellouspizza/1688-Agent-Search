import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import {
  buildToolsMcpConfig,
  installCodexRuntimeMcp,
  parseCodexRuntime,
  replaceManagedCodexBlock,
} from "../dist/codex-runtime.js";

test("Codex runtime aliases preserve auto and app-server values", () => {
  assert.equal(parseCodexRuntime("on"), "codex_app_server");
  assert.equal(parseCodexRuntime("hermes"), "auto");
  assert.throws(() => parseCodexRuntime("legacy"), /auto 或 codex_app_server/);
});

test("Node MCP configuration launches compiled CLI", () => {
  const config = buildToolsMcpConfig({
    cwd: "/project",
    launcherPath: "/runtime/dist/cli-entry.js",
    execPath: "/usr/local/bin/node",
    skillRoot: "/runtime/skills",
    environ: { AGENT_SEARCH_1688_HOME: "/home/app" },
  });
  assert.equal(config.command, "/usr/local/bin/node");
  assert.deepEqual(config.args, ["/runtime/dist/cli-entry.js", "mcp-server"]);
  assert.equal(config.env.AGENT_SEARCH_1688_SKILL_ROOT, "/runtime/skills");
});

test("managed TOML replacement preserves unrelated user configuration", () => {
  const content = replaceManagedCodexBlock('model = "x"\n', {
    command: "/usr/bin/node",
    args: ["/app/cli.js", "mcp-server"],
    env: { AGENT_SEARCH_1688_SKILL_ROOT: "/app/skills" },
    enabled: true,
    startup_timeout_sec: 15,
    tool_timeout_sec: 45,
  });
  assert.match(content, /^model = "x"/);
  assert.equal(content.match(/>>> 1688 Agent Search managed Codex runtime >>>/gu)?.length, 1);
});

test("install writes one atomic managed block", () => {
  const codexHome = mkdtempSync(join(tmpdir(), "as1688-codex-runtime-"));
  const target = join(codexHome, "config.toml");
  writeFileSync(target, 'model = "x"\n');
  installCodexRuntimeMcp({ codexHome, launcherPath: "/app/cli.js", execPath: "/usr/bin/node", skillRoot: "/app/skills" });
  const stored = readFileSync(target, "utf8");
  assert.match(stored, /^model = "x"/);
  assert.equal(stored.match(/mcp_servers\."1688-tools"/gu)?.length, 1);
});
