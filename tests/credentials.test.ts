import assert from "node:assert/strict";
import { chmodSync, lstatSync, mkdtempSync, readFileSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import {
  deleteOpenAiApiKey,
  loadOpenAiApiKey,
  saveOpenAiApiKey,
} from "../dist/credentials.js";

test("environment credentials take precedence and are never persisted", () => {
  const appHome = mkdtempSync(join(tmpdir(), "as1688-creds-"));
  const loaded = loadOpenAiApiKey({ appHome, environ: { OPENAI_API_KEY: "sk-env" }, platform: "linux" });
  assert.deepEqual(loaded, { apiKey: "sk-env", source: "environment:OPENAI_API_KEY" });
});

test("credential files reject symlinks and broad permissions", () => {
  const appHome = mkdtempSync(join(tmpdir(), "as1688-creds-"));
  const target = join(appHome, "target.json");
  writeFileSync(target, '{"openai_api_key":"sk-file"}');
  symlinkSync(target, join(appHome, "credentials.json"));
  assert.throws(() => loadOpenAiApiKey({ appHome, environ: {}, platform: "linux" }), /不能是符号链接/);

  const secondHome = mkdtempSync(join(tmpdir(), "as1688-creds-"));
  const path = join(secondHome, "credentials.json");
  writeFileSync(path, '{"openai_api_key":"sk-file"}', { mode: 0o644 });
  chmodSync(path, 0o644);
  assert.throws(() => loadOpenAiApiKey({ appHome: secondHome, environ: {}, platform: "linux" }), /权限过宽/);
});

test("file credentials save, verify, and delete with mode 0600", () => {
  const appHome = mkdtempSync(join(tmpdir(), "as1688-creds-"));
  assert.equal(saveOpenAiApiKey("sk-file", { appHome, platform: "linux" }), "credential-file");
  const path = join(appHome, "credentials.json");
  assert.equal(lstatSync(path).mode & 0o777, 0o600);
  assert.deepEqual(JSON.parse(readFileSync(path, "utf8")), { openai_api_key: "sk-file" });
  assert.deepEqual(loadOpenAiApiKey({ appHome, environ: {}, platform: "linux" }), { apiKey: "sk-file", source: "credential-file" });
  assert.deepEqual(deleteOpenAiApiKey({ appHome, platform: "linux" }), ["credential-file"]);
});

test("credentials reject empty or whitespace-bearing keys", () => {
  const appHome = mkdtempSync(join(tmpdir(), "as1688-creds-"));
  assert.throws(() => saveOpenAiApiKey("bad key", { appHome, platform: "linux" }), /空白字符/);
});
