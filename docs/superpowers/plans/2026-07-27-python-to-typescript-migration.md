# Python to TypeScript Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Python implementation with a Node.js 24 TypeScript implementation while preserving the CLI, configuration, credentials, SQLite data, provider protocols, project tools, and Hermes-aligned agent behavior.

**Architecture:** The final package is an ESM TypeScript application compiled to `dist/`. It keeps the existing provider-neutral runtime boundary, stores the same rows in the existing SQLite schema through `node:sqlite`, uses built-in `fetch` and `AbortController` for Responses streaming, and uses Playwright for browser inspection. Python files and the ZipApp launcher are removed only after every JavaScript path is wired.

**Tech Stack:** Node.js 24, TypeScript 6, ESM, `node:sqlite`, built-in `fetch`, Node child processes, Node test runner on the separate `agent/python-to-js-tests` branch, Playwright 1.x.

---

## Hermes parity baseline

- Reference repository: `NousResearch/hermes-agent`
- Reference commit: `cb06017b1d6e1b9ae0cb35f99a48ffa6bcbaa828`
- Agent loop reference: `agent/conversation_loop.py::run_conversation`
- Iteration/final-summary reference: `agent/turn_finalizer.py` and `agent/chat_completion_helpers.py::handle_max_iterations`
- Codex Responses reference: `agent/codex_responses_adapter.py`
- Codex app-server reference: `agent/codex_runtime.py` and `agent/transports/codex_app_server*.py`
- Prompt/SOUL reference: `agent/prompt_builder.py::load_soul_md`

The user approved the implementation-level deviations required by TypeScript: Node event-loop concurrency replaces Python threads, `node:sqlite` replaces `sqlite3`, `AbortController` replaces Python cancellation flags, and the installer produces a Node package rather than a ZipApp. Public behavior and wire payloads remain aligned.

## Final file map

```text
src/
├── cli-entry.ts                  executable entry point and exit-code boundary
├── cli.ts                        command parsing, setup, chat loop, slash commands
├── codex-runtime.ts              managed Codex TOML block and MCP launch config
├── config.ts                     non-secret JSON config and environment resolution
├── credentials.ts                OpenAI keychain/file credential lifecycle
├── display.ts                    Hermes-compatible thinking spinner
├── index.ts                      public runtime/model exports
├── models.ts                     shared immutable data contracts and validation
├── prompt-builder.ts             SOUL, base instructions, Skill summary, context
├── runtime.ts                    session state machine and function-call loop
├── session-store.ts              SQLite schema, transactions, recovery, locks
├── soul.ts                       seed/load the one global SOUL.md
├── providers/
│   ├── codex-app-server.ts       JSON-RPC transport and optional provider adapter
│   ├── codex-auth.ts             local Codex OAuth read/refresh with file lock
│   ├── codex-responses.ts        ChatGPT Codex Responses adapter
│   ├── errors.ts                 provider errors and interruption error
│   ├── index.ts                  provider exports and model/provider resolution
│   └── openai.ts                 OpenAI models and Responses/SSE adapter
├── skills/
│   └── catalog.ts                confined project Skill discovery and reads
└── tools/
    ├── mcp-server.ts             stdio MCP JSON-RPC adapter
    ├── registry.ts               schemas, dispatch, parallel-safety metadata
    ├── browser/
    │   └── inspect.ts            constrained Playwright browser operations
    └── web/
        ├── extract.ts            SSRF-safe public HTTP extraction
        ├── search.ts             project registry composition
        └── searxng.ts            SearXNG search adapter
```

Production-only changes stay on `codex/momo/python-to-js`. Tests live in a sibling worktree on `agent/python-to-js-tests`; before each test run that branch merges the latest production commit.

### Task 1: Establish the Node package and shared contracts

**Files:**
- Create: `package.json`
- Create: `package-lock.json`
- Create: `tsconfig.json`
- Create: `src/models.ts`
- Create: `src/config.ts`
- Create: `src/soul.ts`
- Create: `src/prompt-builder.ts`
- Test branch create: `tests/models.test.ts`
- Test branch create: `tests/config.test.ts`

- [ ] **Step 1: Create the dedicated test branch worktree**

Run:

```bash
git branch agent/python-to-js-tests codex/momo/python-to-js
git worktree add /tmp/1688-agent-search-js-tests agent/python-to-js-tests
```

Expected: production work remains in the repository and test-only files live under `/tmp/1688-agent-search-js-tests`.

- [ ] **Step 2: Write failing contract and configuration tests on the test branch**

```ts
import assert from "node:assert/strict";
import { test } from "node:test";
import { validateConversationRoles } from "../src/models.ts";
import { loadPurchaseConfig } from "../src/config.ts";

test("completed messages must alternate user and assistant", () => {
  assert.throws(() => validateConversationRoles([
    { id: "1", sessionId: "s", role: "assistant", content: "x", status: "completed", provider: "p", model: "m", createdAt: "now" },
  ]), /期望 user/);
});

test("config keeps the existing snake_case file contract", () => {
  const config = loadPurchaseConfig({
    configPath: new URL("./fixtures/config.json", import.meta.url),
    environ: {},
  });
  assert.equal(config.maxIterations, 9);
  assert.equal(config.openaiRuntime, "auto");
});
```

- [ ] **Step 3: Run tests and confirm the TypeScript modules do not exist**

Run: `node --test tests/models.test.ts tests/config.test.ts`

Expected: FAIL with `ERR_MODULE_NOT_FOUND`.

- [ ] **Step 4: Add package/build configuration**

```json
{
  "name": "agent-search-1688",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "engines": { "node": ">=24" },
  "bin": {
    "as1688": "dist/cli-entry.js",
    "1688-agent-search": "dist/cli-entry.js"
  },
  "scripts": {
    "build": "tsc -p tsconfig.json",
    "typecheck": "tsc -p tsconfig.json --noEmit"
  },
  "dependencies": { "playwright": ">=1.59.0 <2" },
  "devDependencies": {
    "@types/node": "^24.0.0",
    "typescript": "^6.0.0"
  }
}
```

`tsconfig.json` must use `module`/`moduleResolution: NodeNext`, `target: ES2024`, `strict: true`, `rootDir: src`, `outDir: dist`, `declaration: true`, and `noUncheckedIndexedAccess: true`.

- [ ] **Step 5: Implement shared discriminated unions and validation**

```ts
export type MessageRole = "system" | "user" | "assistant" | "tool";
export type MessageStatus = "pending" | "streaming" | "completed" | "failed" | "interrupted" | "incomplete";
export type ChatStatus = "completed" | "failed" | "interrupted" | "incomplete";
export type ConversationState = "idle" | "preparing" | "requesting" | "streaming" | "completed" | "failed" | "interrupted" | "incomplete";

export interface Message {
  readonly id: string;
  readonly sessionId: string;
  readonly role: MessageRole;
  readonly content: string;
  readonly status: MessageStatus;
  readonly provider: string;
  readonly model: string;
  readonly createdAt: string;
}

export interface TokenUsage { readonly inputTokens: number; readonly outputTokens: number; readonly totalTokens: number; }
export const EMPTY_USAGE: TokenUsage = Object.freeze({ inputTokens: 0, outputTokens: 0, totalTokens: 0 });
```

Define `ChatResult`, `ProviderRuntime`, `ModelOption`, `PurchaseSession`, `ProviderStreamResult`, `ProviderToolCall`, and `ProviderTurnResult` with the same fields as `models.py`, using camelCase internally and explicit wire-format conversion at provider/database boundaries.

- [ ] **Step 6: Implement exact configuration and SOUL compatibility**

The loader maps existing JSON keys without rewriting them:

```ts
const FILE_KEYS = {
  openai_runtime: "openaiRuntime",
  database_path: "databasePath",
  request_timeout_seconds: "requestTimeoutSeconds",
  max_context_characters: "maxContextCharacters",
  searxng_base_url: "searxngBaseUrl",
  searxng_timeout_seconds: "searxngTimeoutSeconds",
  max_iterations: "maxIterations",
} as const;
```

Defaults remain `local-codex-chatgpt`, `gpt-5.6-sol`, `auto`, 600 seconds, 200,000 characters, `http://127.0.0.1:8888`, 30 seconds, and 500 iterations. Atomic config writes use a sibling `.tmp`, mode `0600`, then `renameSync`. `loadOrSeedSoul()` creates only `~/.1688-agent-search/SOUL.md`, never overwrites it, and returns the built-in purchase identity for empty content.

- [ ] **Step 7: Implement the three-part prompt builder**

`PurchasePromptBuilder` exposes `buildBaseInstructions()`, `buildSkillsSystemPrompt()`, `buildContext(history, userInput)`, and `countContextCharacters()`. It preserves the current base/tool guidance byte-for-byte and loads Skill summaries through the catalog introduced in Task 3.

- [ ] **Step 8: Build, merge production into the test branch, and run tests**

Run on production: `npm install && npm run typecheck && npm run build`

Commit production: `feat: establish TypeScript application core`

Run on test branch:

```bash
git merge --no-ff codex/momo/python-to-js
node --test tests/models.test.ts tests/config.test.ts
```

Expected: all tests PASS.

### Task 2: Port SQLite persistence and process/session ownership

**Files:**
- Create: `src/session-store.ts`
- Test branch create: `tests/session-store.test.ts`

- [ ] **Step 1: Write failing compatibility tests on the test branch**

```ts
test("opens the Python-created schema without migration loss", () => {
  const store = new PurchaseSessionStore(databasePath);
  const session = store.getSession("session_existing");
  assert.equal(session.model, "gpt-5.6-sol");
  assert.equal(store.loadContextMessages(session.id).length, 2);
  store.close();
});

test("assistant, request usage, and session update commit atomically", () => {
  const reply = store.saveReply({ sessionId, requestId, content: "done", providerRuntime, actualModel: "gpt-5.6-sol", usage: { inputTokens: 1, outputTokens: 2, totalTokens: 3 }, providerThreadId: "thread_1" });
  assert.equal(reply.status, "completed");
  assert.equal(store.getSession(sessionId).providerThreadId, "thread_1");
});
```

- [ ] **Step 2: Confirm failure before implementation**

Run: `node --test tests/session-store.test.ts`

Expected: FAIL with missing `src/session-store.ts`.

- [ ] **Step 3: Recreate the schema and transaction boundaries with `node:sqlite`**

Use `DatabaseSync`, `PRAGMA foreign_keys = ON`, and `PRAGMA journal_mode = WAL`. Keep table names, column names, checks, indexes, UUID prefixes, 30,000-character trace bounds, and Asia/Shanghai ISO timestamps unchanged. Each multi-row mutation uses:

```ts
this.database.exec("BEGIN IMMEDIATE");
try {
  mutation();
  this.database.exec("COMMIT");
} catch (error) {
  this.database.exec("ROLLBACK");
  throw error;
}
```

- [ ] **Step 4: Implement ownership and session locks**

Use exclusive `openSync(path, "wx", 0o600)` lock files containing `{ pid, startedAt, ownerId }`. A lock is stale only when its PID is absent (`process.kill(pid, 0)` returns `ESRCH`). On startup, recover only pending/streaming requests whose owner lock is stale, mark their user messages and requests `incomplete`, and clear affected provider thread IDs in one transaction. Register `exit`, `SIGINT`, and `SIGTERM` cleanup without swallowing the CLI's own interrupt handling.

- [ ] **Step 5: Port all store methods**

Implement `createOrRestoreSession`, `getSession`, `attachProviderThread`, `loadContextMessages`, `beginRequest`, `markRequestStreaming`, `appendToolTrace`, `saveReply`, `failRequest`, `listSessions`, and `close`. SQL stays identical to the Python implementation; row mapping is the only camelCase conversion boundary.

- [ ] **Step 6: Validate against a Python-created fixture**

Generate the fixture on the test branch using `git show main:src/agent_search_1688/session_store.py` in a temporary Python package, then open it with the TypeScript store. Do not commit generated databases.

- [ ] **Step 7: Commit, merge, and run**

Commit production: `feat: port session persistence to node sqlite`

Expected test command: `node --test tests/session-store.test.ts`

### Task 3: Port credentials, Skill catalog, registry, Web, and Browser tools

**Files:**
- Create: `src/credentials.ts`
- Create: `src/skills/catalog.ts`
- Create: `src/tools/registry.ts`
- Create: `src/tools/web/searxng.ts`
- Create: `src/tools/web/extract.ts`
- Create: `src/tools/web/search.ts`
- Create: `src/tools/browser/inspect.ts`
- Test branch create: `tests/credentials.test.ts`
- Test branch create: `tests/project-capabilities.test.ts`

- [ ] **Step 1: Write failing security and capability tests**

```ts
test("credential file rejects symlinks and group-readable modes", () => {
  assert.throws(() => loadOpenAiApiKey({ environ: {}, platform: "linux" }), /不能是符号链接|权限过宽/);
});

test("skill reads cannot escape a configured root", () => {
  const catalog = new SkillCatalog([skillsRoot]);
  assert.throws(() => catalog.read("sample", "../../secret"), /路径|范围/);
});

test("web extraction rejects loopback and private DNS targets", async () => {
  await assert.rejects(() => validatePublicUrl("http://127.0.0.1/private"), /公开/);
});
```

- [ ] **Step 2: Port credential precedence and mutation semantics**

Preserve `OPENAI_API_KEY → macOS keychain → 0600 credential file`. Invoke `/usr/bin/security` with `spawnSync` argument arrays, pass new keys on stdin, cap calls at 15 seconds, verify after save, and never include key values in errors, logs, config, or session rows.

- [ ] **Step 3: Port the Skill catalog with containment checks**

Resolve every candidate through `realpathSync`; accept it only when `relative(root, candidate)` is neither absolute nor begins with `..`. Discover only `*/SKILL.md`, parse the first Markdown title/description, sort by name, and cap reads to the current catalog limit.

- [ ] **Step 4: Implement registry contracts**

```ts
export interface ToolEntry {
  readonly name: string;
  readonly description: string;
  readonly inputSchema: Record<string, unknown>;
  readonly parallelSafe: boolean;
  readonly handler: (arguments_: Record<string, unknown>) => unknown | Promise<unknown>;
}

export class ToolRegistry {
  register(entry: ToolEntry): void;
  definitions(): McpToolDefinition[];
  dispatch(name: string, arguments_: Record<string, unknown>): Promise<Record<string, unknown>>;
  isParallelSafe(name: string): boolean;
}
```

Reject duplicate names and unknown tools. Definitions retain `inputSchema` exactly for MCP and convert to Responses function schemas only inside provider adapters.

- [ ] **Step 5: Port SearXNG and public URL extraction**

Use `URL`, `dns.promises.lookup({ all: true })`, `net.isIP`, and explicit IPv4/IPv6 private/reserved range checks before every request and redirect. Use `AbortSignal.timeout`, accept only HTTP(S), cap response bytes before parsing, strip scripts/styles, normalize whitespace, and return the existing JSON shape.

- [ ] **Step 6: Port constrained Playwright operations**

Lazily import `playwright`, launch one headless Chromium instance, validate navigation URLs with the same public URL guard, record bounded console/page errors, and expose only `browser_navigate`, `browser_snapshot`, and read-only `browser_console` operations `messages|links`. Do not expose arbitrary page JavaScript.

- [ ] **Step 7: Compose the registry and verify tool definitions**

`buildToolRegistry()` registers `skills_list`, `skill_view`, `web_search`, `web_extract`, and browser tools with the current schemas and parallel-safe flags. Test exact names, required properties, bounds, and `additionalProperties: false`.

- [ ] **Step 8: Commit, merge, and run**

Commit production: `feat: port project capabilities to TypeScript`

Expected: `node --test tests/credentials.test.ts tests/project-capabilities.test.ts` passes.

### Task 4: Port OpenAI and direct Codex Responses providers

**Files:**
- Create: `src/providers/errors.ts`
- Create: `src/providers/codex-auth.ts`
- Create: `src/providers/openai.ts`
- Create: `src/providers/codex-responses.ts`
- Create: `src/providers/index.ts`
- Test branch create: `tests/openai-provider.test.ts`
- Test branch create: `tests/codex-responses.test.ts`

- [ ] **Step 1: Write SSE fixture tests before implementation**

```ts
test("assembles streamed text, function calls, response items, and usage", async () => {
  const result = await adapter.runModelTurn({ inputItems, toolDefinitions, onStreamStarted, onDelta });
  assert.equal(result.content, "找到结果");
  assert.deepEqual(result.toolCalls[0], { callId: "call_1", name: "web_search", arguments: { query: "轴承" } });
  assert.equal(result.usage.totalTokens, 15);
});

test("401 reloads auth then refreshes at most once", async () => {
  await adapter.runModelTurn(request);
  assert.equal(refreshCalls, 1);
  assert.equal(requestCalls, 2);
});
```

- [ ] **Step 2: Implement a byte-safe SSE parser**

Read `Response.body` through `TextDecoderStream`, buffer partial lines, collect consecutive `data:` lines until the blank separator, ignore comments, stop at `[DONE]`, and parse each JSON event once. Abort errors map to `ProviderInterrupted`; non-2xx bodies are truncated and sanitized.

- [ ] **Step 3: Port OpenAI model catalog behavior**

GET `/v1/models`, keep current text-model filtering and ordering, retain display metadata, and distinguish invalid credentials, timeout, malformed JSON, and empty catalog errors without echoing response secrets.

- [ ] **Step 4: Port OpenAI Responses requests**

POST `/v1/responses` with the existing system instructions/input format. Stream `response.output_text.delta`, reconstruct `function_call` items and arguments, capture the terminal response ID/model/usage, reject unsupported non-text items, and return `ProviderTurnResult`.

- [ ] **Step 5: Port local Codex auth safely**

Read `~/.codex/auth.json` only when it is a regular file with safe permissions. Parse JWT claims without verification only to read account/expiry metadata, serialize refresh with an exclusive lock, reload the file before refreshing after a 401, atomically persist rotated tokens with the existing unrelated fields preserved, and never log tokens.

- [ ] **Step 6: Port direct Codex Responses wire differences**

Use `https://chatgpt.com/backend-api/codex/responses`, Codex ChatGPT headers, `store: false`, and the current Responses function schema conversion. Preserve streamed event assembly, response item replay, `function_call_output`, actual model, response ID, and one refresh retry.

- [ ] **Step 7: Commit, merge, and run**

Commit production: `feat: port responses providers to TypeScript`

Expected: provider fixture tests pass without live network access.

### Task 5: Port optional Codex app-server and managed MCP configuration

**Files:**
- Create: `src/providers/codex-app-server.ts`
- Create: `src/codex-runtime.ts`
- Test branch create: `tests/codex-app-server.test.ts`
- Test branch create: `tests/codex-runtime.test.ts`

- [ ] **Step 1: Write JSON-RPC lifecycle tests**

```ts
test("starts lazily at the first turn and completes only after turn/completed", async () => {
  adapter.openSession(session, []);
  assert.equal(spawnCalls, 0);
  const result = await adapter.streamModelReply(request);
  assert.equal(spawnCalls, 1);
  assert.equal(result.content, "done");
});

test("managed TOML replacement preserves user configuration", () => {
  const output = replaceManagedCodexBlock("model = \"x\"\n", block);
  assert.match(output, /^model = "x"/);
  assert.equal(output.match(/1688 Agent Search managed/g)?.length, 1);
});
```

- [ ] **Step 2: Implement newline-delimited JSON-RPC transport**

Spawn `codex app-server` with filtered model-driving environment, write one JSON object per line, correlate request IDs through pending promises, process notifications in order, bound stderr, reject all pending requests on exit, and enforce startup/request/turn timeouts.

- [ ] **Step 3: Implement the optional adapter**

Preserve lazy startup, `initialize`, thread start/resume, turn start, delta assembly, usage updates, approval requests, user interrupt, model switching, and shutdown. Only the current user input is handed to app-server because Codex owns its thread history on this route.

- [ ] **Step 4: Generate a Node MCP launch definition**

The managed TOML uses `process.execPath` as `command`, the compiled `dist/cli-entry.js` as the first argument, and `mcp-server` as the second. In source execution it uses the resolved TypeScript runner only for development; installed output never depends on TypeScript.

- [ ] **Step 5: Commit, merge, and run**

Commit production: `feat: port optional codex app server runtime`

Expected: all app-server tests pass against a fixture child process.

### Task 6: Port the Hermes-aligned Agent runtime

**Files:**
- Create: `src/runtime.ts`
- Create: `src/index.ts`
- Test branch create: `tests/runtime.test.ts`
- Test branch create: `tests/tool-loop.test.ts`

- [ ] **Step 1: Write state-machine and tool-loop tests**

```ts
test("parallel-safe calls dispatch concurrently but replay in model order", async () => {
  const result = await runtime.chat("find products");
  assert.equal(maxConcurrent, 2);
  assert.deepEqual(replayedCallIds, ["call_1", "call_2"]);
  assert.equal(result.status, "completed");
});

test("iteration exhaustion receives a tool-free grace summary", async () => {
  const result = await runtime.chat("research");
  assert.deepEqual(turns.at(-1)?.toolDefinitions, []);
  assert.match(result.content, /summary/);
});

test("duplicate normalized calls fail before second dispatch", async () => {
  const result = await runtime.chat("loop");
  assert.equal(result.status, "failed");
  assert.match(result.error ?? "", /重复调用/);
});
```

- [ ] **Step 2: Implement the provider-neutral state machine**

Allow only the existing transitions. `chat()` validates non-empty input, creates/restores one session, checks the same context-character limit, persists the user request before network access, marks streaming on the first provider event, saves assistant/request/session atomically, maps aborts to `interrupted`, and returns to `idle` in `finally`.

- [ ] **Step 3: Implement the Hermes-aligned function-call loop**

For each of `maxIterations`: request a turn, stop when there are no calls, append response items, normalize argument objects with recursively sorted JSON keys, reject duplicate `(name, normalizedArguments)`, dispatch a fully parallel-safe batch through `Promise.all`, otherwise dispatch in order, persist traces in original call order, and replay `function_call_output` items.

- [ ] **Step 4: Implement iteration exhaustion behavior**

Append the current Hermes summary instruction, send up to two tool-free attempts, buffer their deltas until a non-empty call-free result is confirmed, then emit them once. Preserve interruption. On failure, return the same bounded fallback string rather than failing the full user request.

- [ ] **Step 5: Implement cancellation and lifecycle**

One `AbortController` belongs to the active turn. `stopReply()` aborts it and asks the selected adapter to interrupt. `close()` idempotently closes adapter, browser, store, and lock files. Model switching is allowed only when idle and updates an attached provider thread.

- [ ] **Step 6: Build the provider factory**

`createPurchaseAgent()` selects direct Codex Responses for `local-codex-chatgpt/auto`, Codex app-server for `codex_app_server`, or OpenAI Responses for `openai-api`, and rejects every other provider.

- [ ] **Step 7: Commit, merge, and run**

Commit production: `feat: port agent runtime and Hermes tool loop`

Expected: `node --test tests/runtime.test.ts tests/tool-loop.test.ts` passes.

### Task 7: Port CLI interaction and terminal display

**Files:**
- Create: `src/display.ts`
- Create: `src/cli.ts`
- Create: `src/cli-entry.ts`
- Test branch create: `tests/cli.test.ts`
- Test branch create: `tests/display.test.ts`

- [ ] **Step 1: Write command and interrupt tests**

```ts
test("chat -q prints streamed content and exits zero", async () => {
  const result = await runCli(["chat", "-q", "你好"], dependencies);
  assert.equal(result, 0);
  assert.equal(stdout.text, "回答\n");
});

test("SIGINT during generation interrupts without saving an assistant reply", async () => {
  await controller.interrupt();
  assert.equal(savedReplies, 0);
  assert.equal(failedStatus, "interrupted");
});
```

- [ ] **Step 2: Port the spinner**

Use one unref'ed timer, preserve the Hermes phrase catalog/timing/TTY erasure behavior, stop before printing deltas, and make repeated `start()`/`stop()` safe.

- [ ] **Step 3: Port parser and setup commands without a framework**

Parse `provider`, `model`, `chat`, `sessions`, and hidden `mcp-server` commands with the same flags, defaults, exit codes, and Chinese validation messages. Dependency-inject streams, prompt input, provider catalog, credential functions, and runtime creation so tests never patch global process state.

- [ ] **Step 4: Port interactive chat and slash commands**

Preserve `/model`, `/session`, `/codex-runtime`, `/stop`, `/help`, and `/quit`. Raw terminal input uses `node:readline/promises`; Ctrl+C exits while idle and aborts only the active request while generating.

- [ ] **Step 5: Add the executable boundary**

```ts
#!/usr/bin/env node
import { runPurchaseCli } from "./cli.js";

process.exitCode = await runPurchaseCli(process.argv.slice(2));
```

Only `cli-entry.ts` writes `process.exitCode`; library functions return status codes and never call `process.exit()`.

- [ ] **Step 6: Commit, merge, and run**

Commit production: `feat: port command line interface to TypeScript`

Expected: CLI and display tests pass, and `node dist/cli-entry.js --help` exits zero.

### Task 8: Port MCP stdio and replace installation packaging

**Files:**
- Create: `src/tools/mcp-server.ts`
- Modify: `install.sh`
- Modify: `uninstall.sh`
- Replace: `1688-agent-search`
- Test branch create: `tests/mcp-server.test.ts`
- Test branch create: `tests/install.test.ts`

- [ ] **Step 1: Write MCP protocol and isolated installer tests**

```ts
test("MCP initialize, tools/list, and tools/call return JSON-RPC results", async () => {
  assert.equal(handleMcpMessage({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} }, registry)?.result.protocolVersion, "2024-11-05");
  assert.equal(handleMcpMessage({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} }, registry)?.result.tools.length, 7);
});
```

The install test sets `AS1688_INSTALL_ROOT` and `AS1688_BIN_DIR` to temporary explicit directories, runs the script, invokes `as1688 --help`, uninstalls, and asserts user config/session files remain.

- [ ] **Step 2: Port MCP line processing**

Read stdin through `readline`, parse one JSON-RPC request per line, support `initialize`, `notifications/initialized`, `tools/list`, and `tools/call`, await async handlers, serialize exactly one response line, and route diagnostics only to stderr.

- [ ] **Step 3: Replace ZipApp installation with an atomic Node installation**

The installer checks Node >=24 and npm, downloads or uses the current source, stages under the target directory, runs `npm ci`, builds with `npm run build`, then runs `npm prune --omit=dev`. It copies `dist`, production `node_modules`, `package.json`, and `skills`, then atomically renames the staged installation. The wrapper executes:

```sh
exec node "$AS1688_RUNTIME/dist/cli-entry.js" "$@"
```

It keeps `AGENT_SEARCH_1688_SKILL_ROOT`, existing PATH behavior, explicit temporary targets, and cleanup traps.

- [ ] **Step 4: Update uninstall boundaries**

Remove only the wrapper and Node runtime directory. Preserve `~/.1688-agent-search/config.json`, `credentials.json`, `sessions.db`, and `SOUL.md`, and report this explicitly.

- [ ] **Step 5: Commit, merge, and run**

Commit production: `feat: package the TypeScript CLI for installation`

Expected: MCP and isolated install tests pass on macOS/Linux-compatible shell paths.

### Task 9: Cut over documentation and remove Python

**Files:**
- Modify: `README.md`
- Remove: `pyproject.toml`
- Remove: `src/agent_search_1688/**/*.py`
- Remove/replace: Python contents of `1688-agent-search`
- Modify: `.gitignore` if build artifacts are not already ignored
- Test branch modify: every import that still points at a removed Python-era or intermediate TypeScript module path

- [ ] **Step 1: Update README runtime and installation facts**

Replace Python 3.9/ZipApp/setup references with Node.js 24/TypeScript/ESM. Keep commands, providers, configuration locations, Skill behavior, Hermes parity explanations, SearXNG cautions, and session compatibility. Update the code-reading order to the final TypeScript file map.

- [ ] **Step 2: Remove the obsolete implementation in one cutover**

Delete `pyproject.toml` and `src/agent_search_1688`. Do not retain a Python compatibility launcher or fallback path. Keep the shell launcher name only as a Node launcher for source checkouts.

- [ ] **Step 3: Prove no Python runtime references remain**

Run:

```bash
rg -n "python3|\.pyz|agent_search_1688|pyproject" README.md install.sh uninstall.sh 1688-agent-search src package.json
find src -type f -name '*.py'
```

Expected: no matches except historical documentation under `docs/superpowers/plans/`.

- [ ] **Step 4: Typecheck and smoke test production output**

Run:

```bash
npm ci
npm run typecheck
npm run build
node dist/cli-entry.js --help
node dist/cli-entry.js sessions
```

Expected: every command exits zero using a temporary `AGENT_SEARCH_1688_HOME`.

- [ ] **Step 5: Commit production cutover**

Commit: `refactor: complete Node.js runtime cutover`

### Task 10: Final test-branch verification and pull request

**Files:**
- Test branch: `tests/**/*.test.ts`
- Production branch: no test-only files

- [ ] **Step 1: Merge the exact production head into the test branch**

```bash
git -C /tmp/1688-agent-search-js-tests merge --no-ff codex/momo/python-to-js
```

Expected: test branch contains the exact proposed production commits plus tests.

- [ ] **Step 2: Run the complete isolated suite**

```bash
npm ci
npm run typecheck
node --test tests/**/*.test.ts
npm run build
```

Expected: all tests PASS, typecheck PASS, build PASS.

- [ ] **Step 3: Run installation and live-free protocol smoke tests**

Run installer tests with temporary roots, MCP JSONL fixtures, OpenAI/Codex SSE fixtures, app-server fixture subprocess, Python-created SQLite fixture, Ctrl+C simulation, and public/private URL fixtures. No test may require a real credential or mutate `~/.codex`, the real keychain, or `~/.1688-agent-search`.

- [ ] **Step 4: Commit and push the test branch separately**

Commit: `test: validate TypeScript runtime migration`

Push `agent/python-to-js-tests`; do not merge it into the production PR.

- [ ] **Step 5: Self-review production diff against the approved parity statement**

Verify every external deviation is limited to Node/TypeScript mechanics, the existing JSON/SQLite data opens without conversion, tool schemas and provider payloads match fixtures, no Python fallback remains, and production contains no tests.

- [ ] **Step 6: Push production and open the required PR**

Push `codex/momo/python-to-js`, then create a pull request targeting `main`. Include the Hermes reference commit, deviations, compatibility evidence, full test-branch commit, installation change, and a clear statement that the PR must not be merged until the user explicitly approves.
