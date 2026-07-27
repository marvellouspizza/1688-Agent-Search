import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import {
  DEFAULT_CONFIG,
  loadPurchaseConfig,
  savePurchaseConfig,
  withCodexRuntime,
  withPurchaseModel,
  withPurchaseProvider,
} from "../dist/config.js";
import { loadOrSeedSoul } from "../dist/soul.js";

test("configuration defaults preserve the Python runtime contract", () => {
  const root = mkdtempSync(join(tmpdir(), "as1688-config-"));
  const config = loadPurchaseConfig({ configPath: join(root, "missing.json"), environ: {} });
  assert.deepEqual(config, DEFAULT_CONFIG);
  assert.equal(config.provider, undefined);
  assert.equal(config.model, undefined);
  assert.equal(config.openaiRuntime, "auto");
  assert.equal(config.maxIterations, 500);
});

test("existing snake_case configuration loads and saves without credentials", () => {
  const root = mkdtempSync(join(tmpdir(), "as1688-config-"));
  const path = join(root, "config.json");
  writeFileSync(path, JSON.stringify({
    provider: "openai-api",
    model: "gpt-5.6",
    openai_runtime: "auto",
    database_path: "/tmp/as1688.db",
    request_timeout_seconds: 11,
    max_context_characters: 1234,
    searxng_base_url: "http://127.0.0.1:8888",
    searxng_timeout_seconds: 12,
    max_iterations: 9,
  }));
  const config = loadPurchaseConfig({ configPath: path, environ: {} });
  assert.equal(config.maxIterations, 9);
  assert.equal(config.requestTimeoutSeconds, 11);
  savePurchaseConfig(config, { configPath: path });
  const stored = JSON.parse(readFileSync(path, "utf8"));
  assert.equal(stored.max_iterations, 9);
  assert.equal(stored.request_timeout_seconds, 11);
  assert.equal(stored.credential, undefined);
});

test("configuration rejects unknown, boolean numeric, and invalid runtime values", () => {
  const root = mkdtempSync(join(tmpdir(), "as1688-config-"));
  const path = join(root, "config.json");
  writeFileSync(path, JSON.stringify({ unknown: true }));
  assert.throws(() => loadPurchaseConfig({ configPath: path, environ: {} }), /未知配置项/);
  writeFileSync(path, JSON.stringify({ max_iterations: true }));
  assert.throws(() => loadPurchaseConfig({ configPath: path, environ: {} }), /必须是整数/);
  writeFileSync(path, JSON.stringify({ openai_runtime: "legacy" }));
  assert.throws(() => loadPurchaseConfig({ configPath: path, environ: {} }), /auto 或 codex_app_server/);
});

test("immutable helpers validate providers, models, and runtimes", () => {
  const selected = withPurchaseProvider(DEFAULT_CONFIG, "openai-api", "gpt-5.6");
  assert.equal(selected.provider, "openai-api");
  assert.equal(withPurchaseModel(DEFAULT_CONFIG, "gpt-5.6").provider, "local-codex-chatgpt");
  assert.equal(withCodexRuntime(DEFAULT_CONFIG, "codex_app_server").openaiRuntime, "codex_app_server");
  assert.throws(() => withPurchaseProvider(DEFAULT_CONFIG, "invalid"), /不支持的供应商/);
});

test("SOUL is seeded once and existing content wins", () => {
  const root = mkdtempSync(join(tmpdir(), "as1688-soul-"));
  const first = loadOrSeedSoul({ appHome: root });
  assert.match(first, /采购调研/);
  writeFileSync(join(root, "SOUL.md"), "custom soul\n", "utf8");
  assert.equal(loadOrSeedSoul({ appHome: root }), "custom soul");
});
