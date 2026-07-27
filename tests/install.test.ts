import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { test } from "node:test";

test("installer builds a runnable Node CLI and uninstaller preserves user data", { timeout: 120_000 }, () => {
  const root = mkdtempSync(join(tmpdir(), "as1688-install-test-"));
  const installRoot = join(root, "runtime");
  const binDir = join(root, "bin");
  const userData = join(root, "user-data");
  const environment = {
    ...process.env,
    AS1688_INSTALL_ROOT: installRoot,
    AS1688_BIN_DIR: binDir,
    AS1688_SKIP_PATH_UPDATE: "1",
    AGENT_SEARCH_1688_HOME: userData,
    PATH: `${binDir}:${process.env.PATH ?? ""}`,
  };
  const installed = spawnSync("sh", [resolve("install.sh")], { cwd: resolve("."), env: environment, encoding: "utf8", timeout: 100_000 });
  assert.equal(installed.status, 0, installed.stderr || installed.stdout);
  const help = spawnSync(join(binDir, "as1688"), ["--help"], { env: environment, encoding: "utf8" });
  assert.equal(help.status, 0, help.stderr);
  assert.match(help.stdout, /as1688 chat/);
  mkdirSync(userData, { recursive: true });
  writeFileSync(join(userData, "keep.txt"), "keep", { flag: "w" });
  const installedUninstaller = join(installRoot, "uninstall.sh");
  assert.equal(existsSync(installedUninstaller), true);
  const removed = spawnSync("sh", [installedUninstaller], { cwd: resolve("."), env: environment, encoding: "utf8" });
  assert.equal(removed.status, 0, removed.stderr || removed.stdout);
  assert.equal(existsSync(join(binDir, "as1688")), false);
  assert.equal(existsSync(installRoot), false);
  assert.equal(existsSync(join(userData, "keep.txt")), true);
});

test("uninstaller rejects a home directory with a trailing slash", () => {
  const root = mkdtempSync(join(tmpdir(), "as1688-uninstall-safety-"));
  const syntheticHome = join(root, "home");
  mkdirSync(syntheticHome, { recursive: true });
  const marker = join(syntheticHome, "keep.txt");
  writeFileSync(marker, "keep");
  const removed = spawnSync("sh", [resolve("uninstall.sh")], {
    cwd: resolve("."),
    env: {
      ...process.env,
      HOME: syntheticHome,
      AS1688_INSTALL_ROOT: `${syntheticHome}/`,
      AS1688_BIN_DIR: join(root, "bin"),
    },
    encoding: "utf8",
  });
  assert.notEqual(removed.status, 0);
  assert.equal(existsSync(marker), true);
});
