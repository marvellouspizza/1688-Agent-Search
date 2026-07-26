import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_search_1688.models import ProviderRuntime
from agent_search_1688.providers.codex_responses import (
    CodexResponsesProviderAdapter,
    _responses_tools,
    load_local_codex_chatgpt_auth,
)
from agent_search_1688.config import PurchaseConfig
from agent_search_1688.prompt_builder import PurchasePromptBuilder


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
            "strict": True,
        }])

    def test_local_auth_requires_chatgpt_tokens_without_exposing_them(self):
        with tempfile.TemporaryDirectory() as directory:
            auth_path = Path(directory) / "auth.json"
            auth_path.write_text(json.dumps({
                "auth_mode": "chatgpt",
                "tokens": {"access_token": "access", "refresh_token": "refresh"},
            }))
            with patch("agent_search_1688.providers.codex_responses.CODEX_AUTH_PATH", auth_path):
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
        adapter = CodexResponsesProviderAdapter(runtime, PurchaseConfig(), PurchasePromptBuilder())
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


if __name__ == "__main__":
    unittest.main()
