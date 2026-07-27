import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_search_1688.models import ProviderRuntime
from agent_search_1688.providers.codex_responses import (
    CodexResponsesProviderAdapter,
    _responses_tools,
)
from agent_search_1688.providers.codex_auth import load_local_codex_chatgpt_auth
from agent_search_1688.config import PurchaseConfig
from agent_search_1688.prompt_builder import PurchasePromptBuilder
from agent_search_1688.session_store import PurchaseSessionStore


class CodexResponsesTests(unittest.TestCase):
    def test_registry_definitions_become_strict_responses_functions(self):
        definitions = [{
            "name": "web_search",
            "description": "Search public pages.",
            "inputSchema": {"type": "object", "properties": {}},
        }]
        self.assertEqual(_responses_tools(definitions), [{
            "type": "function",
            "name": "web_search",
            "description": "Search public pages.",
            "parameters": {"type": "object", "properties": {}},
        }])

    def test_local_auth_requires_chatgpt_tokens_without_exposing_them(self):
        with tempfile.TemporaryDirectory() as directory:
            auth_path = Path(directory) / "auth.json"
            auth_path.write_text(json.dumps({
                "auth_mode": "chatgpt",
                "tokens": {"access_token": "access", "refresh_token": "refresh"},
            }))
            with patch(
                "agent_search_1688.providers.codex_auth.CODEX_AUTH_PATH",
                auth_path,
            ):
                self.assertEqual(load_local_codex_chatgpt_auth(), {
                    "access_token": "access", "refresh_token": "refresh",
                })

    def test_function_call_response_is_returned_to_runtime(self):
        runtime = ProviderRuntime(
            provider="local-codex-chatgpt",
            model="gpt-test",
            api_mode="codex_responses",
            base_url="https://chatgpt.com/backend-api/codex",
            credential_source="test",
        )
        adapter = CodexResponsesProviderAdapter(
            runtime, PurchaseConfig(),
            PurchasePromptBuilder(Path(__file__).parents[1] / "skills"),
        )
        adapter._instructions = "test"
        response = {
            "id": "response_1",
            "model": "gpt-test",
            "output": [{
                "type": "function_call",
                "call_id": "call_1",
                "name": "web_search",
                "arguments": '{"query":"construction gate"}',
            }],
            "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
        }
        with patch.object(adapter, "_request", return_value=response):
            result = adapter.run_1688_model_turn(
                input_items=[{"role": "user", "content": "find suppliers"}],
                tool_definitions=[],
                on_stream_started=lambda: None,
                on_delta=lambda _value: None,
            )
        self.assertEqual(result.tool_calls[0].name, "web_search")
        self.assertEqual(result.tool_calls[0].arguments, {"query": "construction gate"})
        self.assertEqual(result.response_items[0]["call_id"], "call_1")
        self.assertEqual(result.usage.total_tokens, 7)

    def test_codex_request_keeps_auto_choice_and_enables_parallel_calls(self):
        runtime = ProviderRuntime(
            "local-codex-chatgpt", "gpt-test", "codex_responses",
            "https://chatgpt.com/backend-api/codex", "test",
        )
        adapter = CodexResponsesProviderAdapter(
            runtime, PurchaseConfig(),
            PurchasePromptBuilder(Path(__file__).parents[1] / "skills"),
        )
        adapter._instructions = "test"
        captured = {}
        def capture(payload):
            captured.update(payload)
            return {"id": "response_1", "model": "gpt-test", "output": []}
        with patch.object(adapter, "_request", side_effect=capture):
            adapter.run_1688_model_turn(
                input_items=[{"role": "user", "content": "find suppliers"}],
                tool_definitions=[{
                    "name": "web_search", "description": "Search public pages.",
                    "inputSchema": {"type": "object", "properties": {}},
                }],
                on_stream_started=lambda: None,
                on_delta=lambda _value: None,
            )
        self.assertEqual(captured["tool_choice"], "auto")
        self.assertTrue(captured["parallel_tool_calls"])

    def test_tool_trace_is_separate_from_chat_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PurchaseSessionStore(Path(directory) / "sessions.db")
            runtime = ProviderRuntime("local-codex-chatgpt", "gpt-test", "codex_responses", "https://example.invalid", "test")
            session = store.create_or_restore_1688_purchase_session(None, runtime)
            user, request_id = store.begin_1688_purchase_request(session.id, "hello", runtime)
            store.append_1688_tool_trace(
                session_id=session.id, request_id=request_id, sequence=1,
                call_id="call_1", name="web_search", arguments_json='{"query":"x"}',
                result_json='{"results":[]}', status="completed", duration_ms=4,
            )
            with store._connect() as connection:
                trace = connection.execute("SELECT name, status FROM tool_traces").fetchone()
                messages = connection.execute("SELECT role FROM messages").fetchall()
            self.assertEqual(tuple(trace), ("web_search", "completed"))
            self.assertEqual([row[0] for row in messages], ["user"])
            store.close()


if __name__ == "__main__":
    unittest.main()
