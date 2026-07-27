import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_search_1688.codex_runtime import (
    MANAGED_END,
    MANAGED_START,
    install_1688_codex_runtime_mcp,
    parse_1688_codex_runtime,
)
from agent_search_1688.cli import _switch_1688_codex_runtime
from agent_search_1688.config import PurchaseConfig
from agent_search_1688.models import (
    Message,
    MessageRole,
    MessageStatus,
    ProviderRuntime,
    PurchaseSession,
)
from agent_search_1688.prompt_builder import PurchasePromptBuilder
from agent_search_1688.providers.codex import (
    CodexAppServerTransport,
    CodexPurchaseProviderAdapter,
    CodexStreamCollector,
    build_1688_codex_turn_request,
    list_1688_codex_models,
    resolve_1688_purchase_provider,
)
from agent_search_1688.providers.codex_responses import (
    CodexResponsesProviderAdapter,
)
from agent_search_1688.runtime import create_1688_purchase_agent
from agent_search_1688.session_store import PurchaseSessionStore


def _runtime(api_mode: str) -> ProviderRuntime:
    return ProviderRuntime(
        provider="local-codex-chatgpt",
        model="gpt-test",
        api_mode=api_mode,
        base_url="https://chatgpt.com/backend-api/codex",
        credential_source="test",
        codex_path="/usr/local/bin/codex",
    )


class _CompletedProcess:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _FakeTransport:
    def __init__(self):
        self.requests = []

    def start_1688_codex_connection(self):
        return None

    def request_1688_codex(self, method, params, **_kwargs):
        self.requests.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}}
        return {}

    def close_1688_codex_connection(self):
        return None


class _UrlResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return json.dumps(self.payload).encode()


class OptionalCodexRuntimeTests(unittest.TestCase):
    def test_runtime_parser_matches_hermes_aliases(self):
        self.assertEqual(parse_1688_codex_runtime("auto"), "auto")
        self.assertEqual(parse_1688_codex_runtime("off"), "auto")
        self.assertEqual(
            parse_1688_codex_runtime("on"),
            "codex_app_server",
        )
        with self.assertRaises(ValueError):
            parse_1688_codex_runtime("browser")

    def test_runtime_switch_updates_saved_next_session_status(self):
        agent = SimpleNamespace(
            config=PurchaseConfig(),
            provider_runtime=_runtime("codex_responses"),
        )
        with patch(
            "agent_search_1688.cli.resolve_1688_purchase_provider",
        ), patch(
            "agent_search_1688.cli.install_1688_codex_runtime_mcp",
            return_value=Path("/tmp/codex/config.toml"),
        ), patch(
            "agent_search_1688.cli.save_1688_purchase_config",
            return_value=Path("/tmp/config.json"),
        ) as save:
            _switch_1688_codex_runtime(agent, "on")
        self.assertEqual(agent.config.openai_runtime, "codex_app_server")
        save.assert_called_once()

    @patch("agent_search_1688.providers.codex.shutil.which", return_value="/codex")
    @patch("agent_search_1688.providers.codex.subprocess.run")
    def test_default_is_direct_and_explicit_switch_is_app_server(
        self,
        run,
        _which,
    ):
        run.side_effect = [
            _CompletedProcess("Logged in using ChatGPT"),
            _CompletedProcess("Logged in using ChatGPT"),
            _CompletedProcess("codex-cli 0.144.0"),
        ]
        direct = resolve_1688_purchase_provider(PurchaseConfig())
        app_server = resolve_1688_purchase_provider(
            PurchaseConfig(openai_runtime="codex_app_server")
        )
        self.assertEqual(direct.api_mode, "codex_responses")
        self.assertEqual(app_server.api_mode, "codex_app_server")

    def test_factory_selects_adapter_by_api_mode(self):
        project_root = Path(__file__).parents[1]
        for api_mode, adapter_type in (
            ("codex_responses", CodexResponsesProviderAdapter),
            ("codex_app_server", CodexPurchaseProviderAdapter),
        ):
            with tempfile.TemporaryDirectory() as directory:
                agent = create_1688_purchase_agent(
                    config=PurchaseConfig(
                        openai_runtime=(
                            "codex_app_server"
                            if api_mode == "codex_app_server"
                            else "auto"
                        )
                    ),
                    provider_runtime=_runtime(api_mode),
                    session_store=PurchaseSessionStore(
                        Path(directory) / "sessions.db"
                    ),
                    cwd=project_root,
                )
                try:
                    self.assertIsInstance(agent.provider_adapter, adapter_type)
                finally:
                    agent.close()

    def test_app_server_requests_match_hermes_minimal_shape(self):
        self.assertEqual(
            build_1688_codex_turn_request(
                thread_id="thread-1",
                user_input="hello",
            ),
            {
                "threadId": "thread-1",
                "input": [{"type": "text", "text": "hello"}],
            },
        )
        project_root = Path(__file__).parents[1]
        adapter = CodexPurchaseProviderAdapter(
            _runtime("codex_app_server"),
            PurchaseConfig(openai_runtime="codex_app_server"),
            PurchasePromptBuilder(project_root / "skills"),
            cwd=project_root,
        )
        fake = _FakeTransport()
        adapter.transport = fake
        session = PurchaseSession(
            "session-1",
            "local-codex-chatgpt",
            "gpt-test",
            "old-provider-thread",
            "now",
            "now",
        )
        history = [
            Message(
                "message-1",
                "session-1",
                MessageRole.USER,
                "old question",
                MessageStatus.COMPLETED,
                "local-codex-chatgpt",
                "gpt-test",
                "now",
            )
        ]
        self.assertEqual(
            adapter.open_1688_purchase_session(session, history),
            "codex_pending_session-1",
        )
        self.assertEqual(fake.requests, [])
        self.assertEqual(adapter._ensure_1688_app_server_thread(), "thread-1")
        self.assertEqual(
            fake.requests[0],
            ("thread/start", {"cwd": str(project_root)}),
        )
        self.assertNotIn("ephemeral", fake.requests[0][1])
        self.assertEqual(fake.requests[1][0], "thread/inject_items")

    def test_app_server_collector_accepts_native_tool_items(self):
        collector = CodexStreamCollector("thread-1", "turn-1", "gpt-test")
        collector.consume_1688_codex_event({
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {"type": "commandExecution", "id": "item-1"},
            },
        })
        collector.consume_1688_codex_event({
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {"type": "agentMessage", "text": "done"},
            },
        })
        collector.consume_1688_codex_event({
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "turn": {"id": "turn-1", "status": "completed"},
            },
        })
        self.assertEqual(collector.complete_1688_codex_stream()[0], "done")

    def test_model_catalog_uses_direct_endpoint_not_app_server(self):
        payload = {
            "models": [
                {"slug": "hidden", "visibility": "hidden", "priority": 0},
                {"slug": "gpt-b", "priority": 2},
                {"slug": "gpt-a", "priority": 1},
            ]
        }
        with patch(
            "agent_search_1688.providers.codex_auth.load_local_codex_chatgpt_auth",
            return_value={"access_token": "token", "refresh_token": "refresh"},
        ), patch(
            "agent_search_1688.providers.codex.urllib.request.urlopen",
            return_value=_UrlResponse(payload),
        ), patch.object(
            CodexAppServerTransport,
            "start_1688_codex_connection",
            side_effect=AssertionError("must not start app-server"),
        ):
            models = list_1688_codex_models(_runtime("codex_responses"))
        self.assertEqual([item.model for item in models], ["gpt-a", "gpt-b"])

    def test_mcp_migration_is_idempotent_and_preserves_user_config(self):
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory) / ".codex"
            codex_home.mkdir()
            config = codex_home / "config.toml"
            config.write_text('model = "gpt-user"\n\n[features]\napps = true\n')
            with patch("sys.argv", [str(Path(directory) / "as1688.pyz")]):
                install_1688_codex_runtime_mcp(
                    cwd=Path(__file__).parents[1],
                    codex_home=codex_home,
                )
                install_1688_codex_runtime_mcp(
                    cwd=Path(__file__).parents[1],
                    codex_home=codex_home,
                )
            rendered = config.read_text()
            self.assertIn('model = "gpt-user"', rendered)
            self.assertIn("[features]", rendered)
            self.assertEqual(rendered.count(MANAGED_START), 1)
            self.assertEqual(rendered.count(MANAGED_END), 1)
            self.assertIn('[mcp_servers."1688-tools"]', rendered)
            self.assertIn("AGENT_SEARCH_1688_SKILL_ROOT", rendered)

    def test_approval_bridge_accepts_project_mcp_and_declines_permissions(self):
        adapter = CodexPurchaseProviderAdapter(
            _runtime("codex_app_server"),
            PurchaseConfig(openai_runtime="codex_app_server"),
            PurchasePromptBuilder(Path(__file__).parents[1] / "skills"),
            cwd=Path(__file__).parents[1],
        )
        sent = []
        adapter.transport.respond_1688_codex = lambda request_id, result: sent.append(
            (request_id, result)
        )
        adapter._handle_1688_codex_server_request({
            "id": 1,
            "method": "mcpServer/elicitation/request",
            "params": {"serverName": "1688-tools"},
        })
        adapter._handle_1688_codex_server_request({
            "id": 2,
            "method": "item/permissions/requestApproval",
            "params": {},
        })
        self.assertEqual(sent[0][1]["action"], "accept")
        self.assertEqual(sent[1][1]["decision"], "decline")


if __name__ == "__main__":
    unittest.main()
