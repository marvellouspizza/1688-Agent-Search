from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from agent_search_1688.config import OPENAI_PROVIDER, PurchaseConfig
from agent_search_1688.models import ProviderRuntime, PurchaseSession
from agent_search_1688.openai_provider import (
    OpenAIResponsesProviderAdapter,
    list_1688_openai_models,
)
from agent_search_1688.prompt_builder import PurchasePromptBuilder
from agent_search_1688.provider import PurchaseInvalidResponse


class FakeHTTPResponse:
    def __init__(self, *, body: bytes = b"", lines: list[bytes] | None = None):
        self.body = body
        self.lines = lines or []
        self.closed = False

    def read(self, _limit: int = -1) -> bytes:
        return self.body

    def __iter__(self):
        return iter(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self) -> None:
        self.closed = True


def _runtime() -> ProviderRuntime:
    return ProviderRuntime(
        provider=OPENAI_PROVIDER,
        model="gpt-5.6",
        api_mode="openai_responses_sse",
        base_url="https://api.openai.com/v1",
        credential_source="test",
        credential="sk-test-secret",
    )


def _sse_lines(events: list[dict]) -> list[bytes]:
    lines: list[bytes] = []
    for event in events:
        lines.append(
            ("data: " + json.dumps(event, ensure_ascii=False) + "\n").encode(
                "utf-8"
            )
        )
        lines.append(b"\n")
    return lines


class OpenAIProviderTests(unittest.TestCase):
    @patch("agent_search_1688.openai_provider.urllib.request.urlopen")
    def test_model_catalog_filters_non_text_models(self, urlopen_mock) -> None:
        urlopen_mock.return_value = FakeHTTPResponse(
            body=json.dumps(
                {
                    "data": [
                        {"id": "gpt-5.6", "owned_by": "openai"},
                        {"id": "text-embedding-3-large", "owned_by": "openai"},
                        {"id": "gpt-4o-realtime-preview", "owned_by": "openai"},
                        {"id": "gpt-3.5-turbo-instruct", "owned_by": "openai"},
                        {"id": "o3-deep-research", "owned_by": "openai"},
                        {"id": "o4-mini-deep-research", "owned_by": "openai"},
                        {"id": "gpt-5-search-preview", "owned_by": "openai"},
                        {"id": "gpt-5-computer-use", "owned_by": "openai"},
                    ]
                }
            ).encode("utf-8")
        )
        models = list_1688_openai_models(_runtime())
        self.assertEqual([model.model for model in models], ["gpt-5.6"])
        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-test-secret")

    @patch("agent_search_1688.openai_provider.urllib.request.urlopen")
    def test_responses_stream_is_parsed_and_tool_free(self, urlopen_mock) -> None:
        events = [
            {"type": "response.created", "response": {"id": "resp_1"}},
            {
                "type": "response.output_item.added",
                "item": {"type": "message"},
            },
            {"type": "response.output_text.delta", "delta": "你"},
            {"type": "response.output_text.delta", "delta": "好"},
            {"type": "response.output_text.done", "text": "你好"},
            {
                "type": "response.output_item.done",
                "item": {"type": "message"},
            },
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_1",
                    "status": "completed",
                    "model": "gpt-5.6",
                    "usage": {
                        "input_tokens": 8,
                        "output_tokens": 2,
                        "total_tokens": 10,
                    },
                },
            },
        ]
        urlopen_mock.return_value = FakeHTTPResponse(lines=_sse_lines(events))
        adapter = OpenAIResponsesProviderAdapter(
            _runtime(),
            PurchaseConfig(model="gpt-5.6"),
            PurchasePromptBuilder(),
        )
        session = PurchaseSession(
            id="session_openai",
            provider=OPENAI_PROVIDER,
            model="gpt-5.6",
            provider_thread_id=None,
            created_at="2026-07-26T00:00:00+08:00",
            updated_at="2026-07-26T00:00:00+08:00",
        )
        adapter.open_1688_purchase_session(session, [])
        deltas: list[str] = []
        started: list[bool] = []
        result = adapter.stream_1688_model_reply(
            user_input="你好",
            user_message_id="msg_1",
            on_stream_started=lambda: started.append(True),
            on_delta=deltas.append,
        )

        self.assertEqual(started, [True])
        self.assertEqual(deltas, ["你", "好"])
        self.assertEqual(result.content, "你好")
        self.assertEqual(result.usage.total_tokens, 10)
        request = urlopen_mock.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["tools"], [])
        self.assertEqual(payload["tool_choice"], "none")
        self.assertFalse(payload["store"])

    @patch("agent_search_1688.openai_provider.urllib.request.urlopen")
    def test_tool_output_item_is_rejected(self, urlopen_mock) -> None:
        urlopen_mock.return_value = FakeHTTPResponse(
            lines=_sse_lines(
                [
                    {
                        "type": "response.output_item.added",
                        "item": {"type": "function_call"},
                    }
                ]
            )
        )
        adapter = OpenAIResponsesProviderAdapter(
            _runtime(),
            PurchaseConfig(model="gpt-5.6"),
            PurchasePromptBuilder(),
        )
        session = PurchaseSession(
            id="session_openai",
            provider=OPENAI_PROVIDER,
            model="gpt-5.6",
            provider_thread_id=None,
            created_at="2026-07-26T00:00:00+08:00",
            updated_at="2026-07-26T00:00:00+08:00",
        )
        adapter.open_1688_purchase_session(session, [])
        with self.assertRaises(PurchaseInvalidResponse):
            adapter.stream_1688_model_reply(
                user_input="调用工具",
                user_message_id="msg_1",
                on_stream_started=lambda: None,
                on_delta=lambda _delta: None,
            )


if __name__ == "__main__":
    unittest.main()
