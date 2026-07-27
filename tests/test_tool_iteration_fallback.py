from pathlib import Path
import tempfile
import unittest

from agent_search_1688.config import PurchaseConfig
from agent_search_1688.models import (
    ProviderRuntime,
    ProviderToolCall,
    ProviderTurnResult,
    TokenUsage,
)
from agent_search_1688.prompt_builder import PurchasePromptBuilder
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
        return self.results.pop(0)

    def switch_1688_purchase_model(self, model):
        self.actual_model = model

    def interrupt_1688_model_reply(self):
        return None

    def close(self):
        return None


def _turn(*, content="", calls=None, response_items=None):
    return ProviderTurnResult(
        content=content,
        tool_calls=calls or [],
        response_items=response_items or [],
        usage=TokenUsage(),
        actual_model="gpt-test",
        response_id="response-test",
        provider_thread_id="provider-test",
    )


class ToolIterationFallbackTests(unittest.TestCase):
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
        project_root = Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as directory:
            store = PurchaseSessionStore(Path(directory) / "sessions.db")
            agent = PurchaseAgentRuntime(
                config=PurchaseConfig(max_iterations=1),
                provider_runtime=provider.provider_runtime,
                session_store=store,
                prompt_builder=PurchasePromptBuilder(project_root / "skills"),
                provider_adapter=provider,
                cwd=project_root,
            )
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
                with store._connect() as connection:
                    traces = connection.execute(
                        "SELECT name, status FROM tool_traces"
                    ).fetchall()
                self.assertEqual(
                    [tuple(row) for row in traces],
                    [("skills_list", "completed")],
                )
            finally:
                agent.close()


if __name__ == "__main__":
    unittest.main()
