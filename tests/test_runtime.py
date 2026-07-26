from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from agent_search_1688.config import PurchaseConfig
from agent_search_1688.models import (
    ChatStatus,
    ProviderRuntime,
    ProviderStreamResult,
    TokenUsage,
)
from agent_search_1688.prompt_builder import PurchasePromptBuilder
from agent_search_1688.runtime import PurchaseAgentRuntime
from agent_search_1688.session_store import PurchaseSessionStore


class FakePurchaseAdapter:
    def __init__(self, provider_runtime: ProviderRuntime):
        self.provider_runtime = provider_runtime
        self.actual_model = provider_runtime.model
        self.thread_id = "thread_fake"
        self.active_turn_id = None
        self.closed = False

    def open_1688_purchase_session(self, _session, _history) -> str:
        return self.thread_id

    def stream_1688_model_reply(
        self,
        *,
        user_input,
        user_message_id,
        on_stream_started,
        on_delta,
    ) -> ProviderStreamResult:
        self.asserted_input = user_input
        self.asserted_message_id = user_message_id
        on_stream_started()
        on_delta("你好")
        on_delta("！")
        return ProviderStreamResult(
            content="你好！",
            usage=TokenUsage(8, 2, 10),
            actual_model=self.actual_model,
            provider_thread_id=self.thread_id,
        )

    def switch_1688_purchase_model(self, model: str) -> None:
        self.actual_model = model
        self.provider_runtime = ProviderRuntime(
            provider=self.provider_runtime.provider,
            model=model,
            api_mode=self.provider_runtime.api_mode,
            base_url=self.provider_runtime.base_url,
            credential_source=self.provider_runtime.credential_source,
            codex_path=self.provider_runtime.codex_path,
        )

    def interrupt_1688_model_reply(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class PurchaseAgentRuntimeTests(unittest.TestCase):
    def test_chat_streams_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider_runtime = ProviderRuntime(
                provider="local-codex-chatgpt",
                model="gpt-test",
                api_mode="codex_app_server_jsonl",
                base_url="local://codex-app-server",
                credential_source="codex-cli-chatgpt-login",
                codex_path="/bin/codex",
                credential="test-memory-secret",
            )
            config = PurchaseConfig(
                model="gpt-test",
                database_path=str(Path(directory) / "sessions.db"),
            )
            store = PurchaseSessionStore(config.resolved_database_path)
            adapter = FakePurchaseAdapter(provider_runtime)
            agent = PurchaseAgentRuntime(
                config=config,
                provider_runtime=provider_runtime,
                session_store=store,
                prompt_builder=PurchasePromptBuilder(),
                provider_adapter=adapter,  # type: ignore[arg-type]
            )
            session = agent.create_or_restore_1688_purchase_session(
                "session_runtime"
            )
            deltas: list[str] = []
            result = agent.chat("你好", on_delta=deltas.append)

            self.assertEqual(result.status, ChatStatus.COMPLETED)
            self.assertEqual(result.content, "你好！")
            self.assertEqual(deltas, ["你好", "！"])
            self.assertEqual(result.usage.total_tokens, 10)
            self.assertEqual(
                agent.provider_runtime.credential,
                "test-memory-secret",
            )
            self.assertEqual(
                len(store.load_1688_purchase_context_messages(session.id)),
                2,
            )
            agent.switch_1688_purchase_model("gpt-switched")
            self.assertEqual(agent.provider_runtime.model, "gpt-switched")
            self.assertEqual(
                store.get_1688_purchase_session(session.id).model,
                "gpt-switched",
            )
            agent.close()


if __name__ == "__main__":
    unittest.main()
