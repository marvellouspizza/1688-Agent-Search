import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { formatWelcomeScreen, runPurchaseCli } from "../dist/cli.js";

function capture() {
  let text = "";
  return {
    stream: { write(chunk: string) { text += chunk; return true; } },
    get text() { return text; },
  };
}

test("welcome screen keeps a fixed width and truncates long labels", () => {
  const screen = formatWelcomeScreen("p".repeat(80), "model", "session");
  const lines = screen.split("\n");
  assert.equal(new Set(lines.map((line) => line.length)).size, 1);
  assert.match(screen, /\.\.\./);
});

test("--help prints usage and exits zero", async () => {
  const stdout = capture();
  const stderr = capture();
  const code = await runPurchaseCli(["--help"], { stdout: stdout.stream, stderr: stderr.stream, environ: {} });
  assert.equal(code, 0);
  assert.match(stdout.text, /as1688 chat/);
  assert.equal(stderr.text, "");
});

test("sessions uses the configured Node SQLite store", async () => {
  const stdout = capture();
  const stderr = capture();
  const appHome = mkdtempSync(join(tmpdir(), "as1688-cli-"));
  const code = await runPurchaseCli(["sessions"], {
    stdout: stdout.stream,
    stderr: stderr.stream,
    environ: { AGENT_SEARCH_1688_HOME: appHome },
  });
  assert.equal(code, 0);
  assert.match(stdout.text, /还没有保存过 Session/);
  assert.equal(stderr.text, "");
});

test("unknown commands return parser exit code two", async () => {
  const stdout = capture();
  const stderr = capture();
  const code = await runPurchaseCli(["unknown"], { stdout: stdout.stream, stderr: stderr.stream, environ: {} });
  assert.equal(code, 2);
  assert.match(stderr.text, /未知命令/);
});
