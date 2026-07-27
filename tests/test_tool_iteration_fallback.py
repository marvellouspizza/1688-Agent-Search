from pathlib import Path
import tempfile
import unittest

from agent_search_1688.config import PurchaseConfig
from agent_search_1688.models import (
    ChatStatus,
    ProviderRuntime,
    ProviderToolCall,
    ProviderTurnResult,
    TokenUsage,
)
from agent_search_1688.prompt_builder import PurchasePromptBuilder
from agent_search_1688.providers.codex import (
    PurchaseProviderError,
    PurchaseProviderInterrupted,
)
from agent_search_1688.runtime import PurchaseAgentRuntime
from agent_search_1688.session_store import PurchaseSessionStore


class _SequenceProvider:
    def __init__(self, results):
        self.provider_runtime = ProviderRuntime(
            "local-codex-chatgpt",
            "gpt-test",
            "codex_responses",
            "https://example.invalid",
            "test",
        )
        self.actual_model = "gpt-test"
        self.thread_id = "provider-test"
        self.results = list(results)
        self.calls = []

    def open_1688_purchase_session(self, session, history):
        return self.thread_id

    def run_1688_model_turn(self, **kwargs):
        self.calls.append(kwargs)
        item = self.results.pop(0)
        deltas = []
        if isinstance(item, tuple):
            item, deltas = item
        for delta in deltas:
            kwargs["on_delta"](delta)
        if isinstance(item, Exception):
            raise item
        return item

    def switch_1688_purchase_model(self, model):
        self.actual_model = model

    def interrupt_1688_model_reply(self):
        return None

    def close(self):
        return None


def _turn(
    *,
    content="",
    calls=None,
    response_items=None,
    total_tokens=0,
    response_id="response-test",
    provider_thread_id="provider-test",
):
    return ProviderTurnResult(
        content=content,
        tool_calls=calls or [],
        response_items=response_items or [],
        usage=TokenUsage(total_tokens=total_tokens),
        actual_model="gpt-test",
        response_id=response_id,
        provider_thread_id=provider_thread_id,
    )


class ToolIterationFallbackTests(unittest.TestCase):
    def _agent(self, directory, provider):
        project_root = Path(__file__).parents[1]
        store = PurchaseSessionStore(Path(directory) / "sessions.db")
        return PurchaseAgentRuntime(
            config=PurchaseConfig(max_iterations=1),
            provider_runtime=provider.provider_runtime,
            session_store=store,
            prompt_builder=PurchasePromptBuilder(project_root / "skills"),
            provider_adapter=provider,
            cwd=project_root,
        )

    def test_iteration_limit_dispatches_last_tools_then_requests_tool_free_summary(self):
        tool_call = ProviderToolCall("call-1", "skills_list", {})
        provider = _SequenceProvider([
            _turn(
                calls=[tool_call],
                response_items=[{
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "skills_list",
                    "arguments": "{}",
                }],
            ),
            _turn(content="已有结果总结"),
        ])
        with tempfile.TemporaryDirectory() as directory:
            agent = self._agent(directory, provider)
            try:
                result = agent.chat("读取 Skill")
                self.assertEqual(result.content, "已有结果总结")
                self.assertEqual(len(provider.calls), 2)
                self.assertTrue(provider.calls[0]["tool_definitions"])
                self.assertEqual(provider.calls[1]["tool_definitions"], [])
                self.assertIn(
                    "without calling any more tools",
                    provider.calls[1]["input_items"][-1]["content"],
                )
                with agent.session_store._connect() as connection:
                    traces = connection.execute(
                        "SELECT name, status FROM tool_traces"
                    ).fetchall()
                self.assertEqual(
                    [tuple(row) for row in traces],
                    [("skills_list", "completed")],
                )
            finally:
                agent.close()

    def test_summary_interruption_remains_interrupted(self):
        provider = _SequenceProvider([
            _turn(
                calls=[ProviderToolCall("call-1", "skills_list", {})],
                response_items=[{
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "skills_list",
                    "arguments": "{}",
                }],
            ),
            PurchaseProviderInterrupted("用户已停止回复"),
        ])
        with tempfile.TemporaryDirectory() as directory:
            agent = self._agent(directory, provider)
            try:
                result = agent.chat("读取 Skill")
                self.assertEqual(result.status, ChatStatus.INTERRUPTED)
                self.assertEqual(result.error, "用户已停止回复")
            finally:
                agent.close()

    def test_discarded_summary_attempt_does_not_emit_its_deltas(self):
        provider = _SequenceProvider([
            _turn(
                calls=[ProviderToolCall("call-1", "skills_list", {})],
                response_items=[{
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "skills_list",
                    "arguments": "{}",
                }],
            ),
            (_turn(), ["discarded"]),
            (_turn(content="accepted"), ["accepted"]),
        ])
        emitted = []
        with tempfile.TemporaryDirectory() as directory:
            agent = self._agent(directory, provider)
            try:
                result = agent.chat("读取 Skill", on_delta=emitted.append)
                self.assertEqual(result.content, "accepted")
                self.assertEqual(emitted, ["accepted"])
            finally:
                agent.close()

    def test_empty_summary_fallback_uses_latest_summary_metadata(self):
        provider = _SequenceProvider([
            _turn(
                calls=[ProviderToolCall("call-1", "skills_list", {})],
                response_items=[{
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "skills_list",
                    "arguments": "{}",
                }],
                response_id="tool-response",
            ),
            _turn(response_id="summary-one", provider_thread_id="summary-one"),
            _turn(
                total_tokens=9,
                response_id="summary-two",
                provider_thread_id="summary-two",
            ),
        ])
        with tempfile.TemporaryDirectory() as directory:
            agent = self._agent(directory, provider)
            try:
                result = agent.chat("读取 Skill")
                self.assertEqual(result.status, ChatStatus.COMPLETED)
                self.assertEqual(result.usage.total_tokens, 9)
                self.assertEqual(agent.session.provider_thread_id, "summary-two")
            finally:
                agent.close()

    def test_failed_summary_discards_partial_delta_and_returns_fallback(self):
        provider = _SequenceProvider([
            _turn(
                calls=[ProviderToolCall("call-1", "skills_list", {})],
                response_items=[{
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "skills_list",
                    "arguments": "{}",
                }],
            ),
            (PurchaseProviderError("summary failed"), ["discarded"]),
        ])
        emitted = []
        with tempfile.TemporaryDirectory() as directory:
            agent = self._agent(directory, provider)
            try:
                result = agent.chat("读取 Skill", on_delta=emitted.append)
                self.assertEqual(result.status, ChatStatus.COMPLETED)
                self.assertNotIn("discarded", "".join(emitted))
                self.assertIn("summary failed", result.content)
            finally:
                agent.close()


if __name__ == "__main__":
    unittest.main()
