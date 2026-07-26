from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from agent_search_1688.models import (
    MessageStatus,
    ProviderRuntime,
    TokenUsage,
)
from agent_search_1688.session_store import PurchaseSessionStore


def runtime() -> ProviderRuntime:
    return ProviderRuntime(
        provider="local-codex-chatgpt",
        model="gpt-test",
        api_mode="codex_app_server_jsonl",
        base_url="local://codex-app-server",
        credential_source="codex-cli-chatgpt-login",
        codex_path="/bin/codex",
    )


class PurchaseSessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "sessions.db"
        self.store = PurchaseSessionStore(database_path)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def test_successful_turn_is_saved_as_a_pair(self) -> None:
        session = self.store.create_or_restore_1688_purchase_session(
            "session_test",
            runtime(),
        )
        user, request_id = self.store.begin_1688_purchase_request(
            session.id,
            "你好",
            runtime(),
        )
        assistant = self.store.save_1688_purchase_reply(
            session_id=session.id,
            request_id=request_id,
            content="你好！",
            provider_runtime=runtime(),
            actual_model="gpt-test",
            usage=TokenUsage(10, 2, 12),
            provider_thread_id="thread_1",
        )

        messages = self.store.load_1688_purchase_context_messages(session.id)
        self.assertEqual([item.id for item in messages], [user.id, assistant.id])
        restored = self.store.get_1688_purchase_session(session.id)
        self.assertEqual(restored.provider_thread_id, "thread_1")

    def test_failed_request_does_not_enter_context(self) -> None:
        session = self.store.create_or_restore_1688_purchase_session(
            "session_test",
            runtime(),
        )
        user, request_id = self.store.begin_1688_purchase_request(
            session.id,
            "失败问题",
            runtime(),
        )
        self.store.fail_1688_purchase_request(
            request_id=request_id,
            user_message_id=user.id,
            status=MessageStatus.FAILED,
            error="safe error",
        )

        self.assertEqual(
            self.store.load_1688_purchase_context_messages(session.id),
            [],
        )
        with sqlite3.connect(self.store.database_path) as connection:
            assistant_count = connection.execute(
                "SELECT COUNT(*) FROM messages WHERE role = 'assistant'"
            ).fetchone()[0]
        self.assertEqual(assistant_count, 0)

    def test_crash_leftover_is_recovered_as_incomplete(self) -> None:
        session = self.store.create_or_restore_1688_purchase_session(
            "session_crash",
            runtime(),
        )
        self.store.attach_1688_provider_thread(
            session.id,
            "thread_will_be_discarded",
            "gpt-test",
        )
        self.store.begin_1688_purchase_request(
            session.id,
            "进程随后崩溃",
            runtime(),
        )
        self.store.close()

        recovered_store = PurchaseSessionStore(self.store.database_path)
        try:
            self.assertEqual(
                recovered_store.load_1688_purchase_context_messages(session.id),
                [],
            )
            self.assertIsNone(
                recovered_store.get_1688_purchase_session(
                    session.id
                ).provider_thread_id
            )
            with sqlite3.connect(self.store.database_path) as connection:
                request_status = connection.execute(
                    "SELECT status FROM requests WHERE session_id = ?",
                    (session.id,),
                ).fetchone()[0]
            self.assertEqual(request_status, "incomplete")
        finally:
            recovered_store.close()

    def test_live_owner_is_not_recovered_by_second_cli(self) -> None:
        session = self.store.create_or_restore_1688_purchase_session(
            "session_live",
            runtime(),
        )
        self.store.attach_1688_provider_thread(
            session.id,
            "thread_live",
            "gpt-test",
        )
        self.store.begin_1688_purchase_request(
            session.id,
            "仍在生成",
            runtime(),
        )

        second_store = PurchaseSessionStore(self.store.database_path)
        try:
            self.assertEqual(
                second_store.load_1688_purchase_context_messages(session.id),
                [],
            )
            self.assertEqual(
                second_store.get_1688_purchase_session(
                    session.id
                ).provider_thread_id,
                "thread_live",
            )
            with sqlite3.connect(self.store.database_path) as connection:
                request_status = connection.execute(
                    "SELECT status FROM requests WHERE session_id = ?",
                    (session.id,),
                ).fetchone()[0]
            self.assertEqual(request_status, "pending")
        finally:
            second_store.close()

    def test_same_session_cannot_be_opened_twice(self) -> None:
        self.store.acquire_1688_session_lock("session_locked")
        second_store = PurchaseSessionStore(self.store.database_path)
        try:
            with self.assertRaisesRegex(RuntimeError, "另一个 CLI"):
                second_store.acquire_1688_session_lock("session_locked")
        finally:
            second_store.close()


if __name__ == "__main__":
    unittest.main()
