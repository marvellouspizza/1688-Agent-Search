import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { SkillCatalog } from "../dist/skills/catalog.js";
import { ToolRegistry } from "../dist/tools/registry.js";
import { validatePublicUrl } from "../dist/tools/web/extract.js";
import { parseSearxngResults, validateSearxngBaseUrl } from "../dist/tools/web/searxng.js";
import { buildToolRegistry } from "../dist/tools/web/search.js";

function skillRoot(): string {
  const root = mkdtempSync(join(tmpdir(), "as1688-skills-"));
  const skill = join(root, "sample");
  mkdirSync(join(skill, "references"), { recursive: true });
  writeFileSync(join(skill, "SKILL.md"), "---\nname: sample\ndescription: Sample workflow\n---\n# Sample\n");
  writeFileSync(join(skill, "references", "guide.md"), "guide\n");
  return root;
}

test("Skill catalog lists, reads, and contains project files", () => {
  const root = skillRoot();
  const catalog = new SkillCatalog([root]);
  assert.deepEqual(catalog.list().map(({ name, description }) => ({ name, description })), [
    { name: "sample", description: "Sample workflow" },
  ]);
  assert.equal(catalog.read("sample", "references/guide.md"), "guide\n");
  assert.throws(() => catalog.read("sample", "../../secret"), /超出 Skill 目录/);
});

test("registry rejects duplicate and unknown tools and supports async handlers", async () => {
  const registry = new ToolRegistry();
  registry.register({
    name: "sample",
    description: "sample",
    inputSchema: { type: "object" },
    parallelSafe: true,
    handler: async (arguments_) => ({ value: arguments_.value }),
  });
  await assert.doesNotReject(() => registry.dispatch("sample", { value: 1 }));
  assert.equal(registry.isParallelSafe("sample"), true);
  assert.throws(() => registry.register({
    name: "sample", description: "again", inputSchema: {}, parallelSafe: false, handler: () => ({}),
  }), /重复/);
  await assert.rejects(() => registry.dispatch("missing", {}), /未注册工具/);
});

test("SearXNG parsing keeps unique public result URLs", () => {
  assert.equal(validateSearxngBaseUrl("http://127.0.0.1:8888/"), "http://127.0.0.1:8888");
  assert.throws(() => validateSearxngBaseUrl("http://127.0.0.1:8888/path"), /不能包含路径/);
  assert.deepEqual(parseSearxngResults({ results: [
    { title: " A ", url: "https://example.com/a", content: " X ", engine: "e" },
    { title: "duplicate", url: "https://example.com/a" },
    { title: "bad", url: "ftp://example.com/x" },
  ] }, 10), [{ title: "A", url: "https://example.com/a", snippet: "X", engine: "e" }]);
});

test("public URL validation rejects loopback, credentials, and non-http schemes", async () => {
  await assert.rejects(() => validatePublicUrl("http://127.0.0.1/private"), /私有或保留/);
  await assert.rejects(() => validatePublicUrl("http://user:pass@example.com"), /公开 HTTP/);
  await assert.rejects(() => validatePublicUrl("file:///etc/passwd"), /公开 HTTP/);
});

test("public URL validation handles public IPv4 and IPv4-mapped IPv6 without weakening private-address checks", async () => {
  assert.equal(
    await validatePublicUrl("https://203.119.169.229/product"),
    "https://203.119.169.229/product",
  );
  assert.equal(
    await validatePublicUrl("https://[::ffff:203.119.169.229]/product"),
    "https://[::ffff:cb77:a9e5]/product",
  );
  await assert.rejects(() => validatePublicUrl("http://[::ffff:127.0.0.1]/private"), /私有或保留/);
});

test("composed project registry exposes the exact seven tools", () => {
  const registry = buildToolRegistry({
    skillRoot: skillRoot(),
    config: {
      openaiRuntime: "auto",
      requestTimeoutSeconds: 3,
      maxContextCharacters: 120_000,
      searxngBaseUrl: "http://127.0.0.1:8888",
      searxngTimeoutSeconds: 3,
      maxIterations: 500,
    },
  });
  assert.deepEqual(registry.definitions().map((tool) => tool.name), [
    "web_search", "web_extract", "browser_navigate", "browser_snapshot", "browser_console", "skills_list", "skill_view",
  ]);
  registry.close();
});
