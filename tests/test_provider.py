from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_search_1688.provider import (
    CodexStreamCollector,
    PurchaseInvalidResponse,
    PurchaseProviderError,
    _discover_1688_codex_mcp_config,
    build_1688_codex_history_items,
    build_1688_codex_turn_request,
)
from agent_search_1688.models import Message, MessageRole, MessageStatus


class CodexProviderContractTests(unittest.TestCase):
    def test_codex_effective_mcp_list_handles_literal_toml_keys(self) -> None:
        codex_path = shutil.which("codex")
        if codex_path is None:
            self.skipTest("本机没有 Codex CLI")

        configurations = [
            (
                "[mcp_servers.'single-table']\n"
                'command = "/usr/bin/true"\n',
                "single-table",
            ),
            (
                "mcp_servers.'single.dotted'.command = "
                '"/usr/bin/true"\n',
                "single.dotted",
            ),
            (
                "[mcp_servers]\n"
                "'single-entry' = { command = \"/usr/bin/true\" }\n",
                "single-entry",
            ),
        ]
        for contents, expected_name in configurations:
            with self.subTest(expected_name=expected_name):
                with tempfile.TemporaryDirectory() as directory:
                    config_path = Path(directory) / "config.toml"
                    config_path.write_text(contents, encoding="utf-8")
                    with patch.dict(
                        os.environ,
                        {"CODEX_HOME": directory},
                        clear=False,
                    ):
                        mcp_config = _discover_1688_codex_mcp_config(
                            codex_path,
                            Path(directory),
                        )
                self.assertIn(expected_name, mcp_config)
                self.assertFalse(mcp_config[expected_name]["enabled"])
                self.assertEqual(
                    mcp_config[expected_name]["command"],
                    "/usr/bin/true",
                )

    @patch("agent_search_1688.provider.subprocess.run")
    def test_invalid_codex_mcp_catalog_fails_closed(self, run_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=["codex", "mcp", "list", "--json"],
            returncode=0,
            stdout=json.dumps([{"enabled": True}]),
            stderr="",
        )
        with self.assertRaises(PurchaseProviderError):
            _discover_1688_codex_mcp_config("codex", Path.cwd())

    def test_turn_request_shape(self) -> None:
        request = build_1688_codex_turn_request(
            thread_id="thread_1",
            user_input="你好",
            model="gpt-test",
            user_message_id="msg_1",
        )
        self.assertEqual(
            request,
            {
                "threadId": "thread_1",
                "input": [{"type": "text", "text": "你好"}],
                "model": "gpt-test",
                "clientUserMessageId": "msg_1",
                "approvalPolicy": "on-request",
            },
        )

    def test_recovery_history_keeps_real_roles(self) -> None:
        common = {
            "session_id": "session_1",
            "status": MessageStatus.COMPLETED,
            "provider": "local-codex-chatgpt",
            "model": "gpt-test",
            "created_at": "2026-07-25T00:00:00+08:00",
        }
        history = [
            Message(
                id="msg_1",
                role=MessageRole.USER,
                content="用户原文",
                **common,
            ),
            Message(
                id="msg_2",
                role=MessageRole.ASSISTANT,
                content="助手原文",
                **common,
            ),
        ]
        self.assertEqual(
            build_1688_codex_history_items(history),
            [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "用户原文"}
                    ],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "助手原文"}
                    ],
                },
            ],
        )

    def test_stream_deltas_require_turn_completed(self) -> None:
        collector = CodexStreamCollector("thread_1", "turn_1", "gpt-test")
        delta_events = [
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread_1",
                    "turnId": "turn_1",
                    "delta": part,
                },
            }
            for part in ["你", "好", "，1688"]
        ]
        for event in delta_events:
            collector.consume_1688_codex_event(event)
        with self.assertRaises(PurchaseInvalidResponse):
            collector.complete_1688_codex_stream()

        collector.consume_1688_codex_event(
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread_1",
                    "turnId": "turn_1",
                    "completedAtMs": 1,
                    "item": {
                        "id": "item_1",
                        "type": "agentMessage",
                        "text": "你好，1688",
                    },
                },
            }
        )
        collector.consume_1688_codex_event(
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": "thread_1",
                    "turnId": "turn_1",
                    "tokenUsage": {
                        "last": {
                            "inputTokens": 10,
                            "outputTokens": 3,
                            "totalTokens": 13,
                        }
                    },
                },
            }
        )
        collector.consume_1688_codex_event(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread_1",
                    "turn": {
                        "id": "turn_1",
                        "items": [],
                        "status": "completed",
                    },
                },
            }
        )
        content, usage, model = collector.complete_1688_codex_stream()
        self.assertEqual(content, "你好，1688")
        self.assertEqual(usage.total_tokens, 13)
        self.assertEqual(model, "gpt-test")

    def test_tool_item_is_rejected(self) -> None:
        collector = CodexStreamCollector("thread_1", "turn_1", "gpt-test")
        with self.assertRaises(PurchaseInvalidResponse):
            collector.consume_1688_codex_event(
                {
                    "method": "item/started",
                    "params": {
                        "threadId": "thread_1",
                        "turnId": "turn_1",
                        "item": {
                            "id": "item_1",
                            "type": "commandExecution",
                        },
                    },
                }
            )


if __name__ == "__main__":
    unittest.main()
