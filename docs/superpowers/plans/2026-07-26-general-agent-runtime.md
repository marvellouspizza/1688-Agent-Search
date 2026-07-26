# Hermes-aligned General Agent Runtime Plan

**Goal:** Make `local-codex-chatgpt` use the Hermes-style `codex_responses` path: this project owns the tool loop and capabilities while using the locally stored Codex ChatGPT OAuth session. The Codex app-server adapter remains only an explicit compatibility provider.

**Architecture:** `PurchaseAgentRuntime` owns the conversation, tool budget, tool trace, Skill catalog and tool registry. `CodexResponsesProviderAdapter` translates this history and registered tool schemas into Codex Responses input, streams output, and returns function calls for the runtime to dispatch. OAuth credential reads and refreshes are serialized against `~/.codex/auth.json`; credentials never enter the session database or logs.

**Compatibility boundary:** The direct Codex backend is an upstream Hermes-supported integration rather than a documented stable third-party Platform API. Pin its protocol behaviour behind one provider module and test it with recorded response fixtures. Do not expose Codex desktop Skills, Browser, Plugins, Shell, or native Web Search.

## 1. Replace the default Codex transport

**Files:**
- Create `src/agent_search_1688/providers/codex_responses.py`
- Modify `src/agent_search_1688/providers/codex.py`
- Modify `src/agent_search_1688/providers/__init__.py`
- Modify `src/agent_search_1688/runtime.py`

- [ ] Resolve `local-codex-chatgpt` to `api_mode="codex_responses"` and `https://chatgpt.com/backend-api/codex`.
- [ ] Read only the required fields from `~/.codex/auth.json`; validate permissions, ChatGPT auth mode and token shape without logging values.
- [ ] Implement a file lock plus atomic write-through for rotating OAuth tokens; on 401, reload fresh file credentials before attempting one refresh.
- [ ] Keep the app-server adapter under an explicit `codex-app-server-compat` provider, never the default.

## 2. Add a provider-neutral function-call loop

**Files:**
- Modify `src/agent_search_1688/runtime.py`
- Modify `src/agent_search_1688/models.py`
- Modify `src/agent_search_1688/session_store.py`
- Modify `src/agent_search_1688/tools/registry.py`

- [ ] Make the provider return structured text, function calls, response items and usage rather than a final text-only reply.
- [ ] Convert registry definitions to Responses `function` schemas; validate and dispatch each call in-process.
- [ ] Replay each call and its `function_call_output` in the next Responses request.
- [ ] Enforce maximum calls, elapsed time, cancellation, duplicate normalized calls and bounded tool-output size.
- [ ] Persist append-only tool trace rows separately from user/assistant messages.

## 3. Build project-owned generic capabilities

**Files:**
- Create `src/agent_search_1688/skills/__init__.py`
- Create `src/agent_search_1688/skills/catalog.py`
- Create `src/agent_search_1688/skills/loader.py`
- Create `src/agent_search_1688/tools/web/extract.py`
- Create `src/agent_search_1688/tools/browser/__init__.py`
- Create `src/agent_search_1688/tools/browser/inspect.py`
- Modify `src/agent_search_1688/tools/web/search.py`

- [ ] Implement project-local `skills_list` and contained `skill_view`; discover `SKILL.md` only under configured roots.
- [ ] Keep SearXNG as project `web_search`, and add guarded public HTTP(S) `web_extract`.
- [ ] Add project Browser navigation and text snapshot behind a constrained adapter; screenshot/vision is separate and opt-in.

## 4. Test the exact proposed behaviour

**Files:**
- Create `tests/test_codex_responses.py`
- Create `tests/test_skills.py`
- Create `tests/test_tools.py`
- Modify/add session-store tests

- [ ] Use mocked HTTP/SSE fixtures for tool-call → output → final-answer, auth reload, and refresh failure.
- [ ] Test tool budgets, trace ordering, Skill containment, URL/private-network guards and MCP compatibility.
- [ ] Before adding/running test-only changes, create/update `agent/general-agent-runtime-tests` from the latest implementation commit as required by `AGENTS.md`.
