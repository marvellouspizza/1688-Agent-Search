from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import os
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from agent_search_1688.cli import (
    _format_1688_welcome_screen,
    run_1688_chat_command,
    run_1688_provider_command,
    run_1688_purchase_cli,
)
from agent_search_1688.config import (
    CODEX_PROVIDER,
    OPENAI_PROVIDER,
    PurchaseConfig,
)
from agent_search_1688.models import ModelOption, ProviderRuntime


def _provider_runtime() -> ProviderRuntime:
    return ProviderRuntime(
        provider="local-codex-chatgpt",
        model="gpt-5.6-sol",
        api_mode="codex_app_server_jsonl",
        base_url="local://codex-app-server",
        credential_source="codex-cli-chatgpt-login",
        codex_path="/bin/codex",
    )


def _models() -> list[ModelOption]:
    return [
        ModelOption(
            model="gpt-5.6-sol",
            display_name="GPT-5.6-Sol",
            description="test",
            is_default=True,
        )
    ]


class PurchaseCliTests(unittest.TestCase):
    @patch("agent_search_1688.cli.run_1688_chat_command", return_value=0)
    def test_no_arguments_enters_chat(self, chat_mock) -> None:
        self.assertEqual(run_1688_purchase_cli([]), 0)
        self.assertEqual(chat_mock.call_args.args[0].command, "chat")

    @patch("agent_search_1688.cli.run_1688_model_command", return_value=0)
    def test_model_subcommand_is_preserved(self, model_mock) -> None:
        self.assertEqual(run_1688_purchase_cli(["model"]), 0)
        self.assertEqual(model_mock.call_args.args[0].command, "model")

    @patch("agent_search_1688.cli.run_1688_provider_command", return_value=0)
    def test_provider_subcommand_is_preserved(self, provider_mock) -> None:
        self.assertEqual(run_1688_purchase_cli(["provider"]), 0)
        self.assertEqual(provider_mock.call_args.args[0].command, "provider")

    def test_provider_key_actions_are_mutually_exclusive(self) -> None:
        with self.assertRaises(SystemExit):
            run_1688_purchase_cli(
                ["provider", "--update-key", "--delete-key"]
            )

    def test_welcome_screen_is_fixed_width_and_dynamic(self) -> None:
        unconfigured = _format_1688_welcome_screen(None, None, None)
        configured = _format_1688_welcome_screen(
            "Local Codex / ChatGPT",
            "GPT-5.6-Sol",
            "New conversation",
        )
        self.assertIn("Provider: Not configured", unconfigured)
        self.assertIn("Model   : Not configured", unconfigured)
        self.assertIn("Session : Waiting for model", unconfigured)
        self.assertIn("Model   : GPT-5.6-Sol", configured)
        self.assertTrue(all(len(line) == 52 for line in configured.splitlines()))

    @patch("agent_search_1688.cli.load_1688_purchase_config")
    def test_first_run_can_cancel_without_saving_model(
        self,
        load_mock,
    ) -> None:
        load_mock.return_value = PurchaseConfig()
        arguments = argparse.Namespace(model=None, question=None, session=None)

        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True):
            with patch("builtins.input", return_value=""):
                with patch("agent_search_1688.cli.save_1688_purchase_config") as save_mock:
                    with redirect_stdout(output):
                        result = run_1688_chat_command(arguments)

        self.assertEqual(result, 0)
        self.assertIn("Model   : Not configured", output.getvalue())
        save_mock.assert_not_called()

    @patch("agent_search_1688.cli.create_1688_purchase_agent")
    @patch("agent_search_1688.cli.PurchaseSessionStore")
    @patch("agent_search_1688.cli.save_1688_purchase_config")
    @patch("agent_search_1688.cli._resolve_1688_provider_and_models")
    @patch("agent_search_1688.cli.load_1688_purchase_config")
    def test_first_model_choice_is_saved_before_chat(
        self,
        load_mock,
        resolve_mock,
        save_mock,
        _store_mock,
        create_agent_mock,
    ) -> None:
        load_mock.return_value = PurchaseConfig()
        resolve_mock.return_value = (_provider_runtime(), _models())
        fake_agent = MagicMock()
        fake_agent.provider_runtime = _provider_runtime()
        fake_agent.create_or_restore_1688_purchase_session.return_value = (
            SimpleNamespace(id="session_test")
        )
        create_agent_mock.return_value = fake_agent
        arguments = argparse.Namespace(model=None, question=None, session=None)

        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "builtins.input",
                side_effect=["1", "1", EOFError()],
            ):
                with redirect_stdout(output):
                    result = run_1688_chat_command(arguments)

        self.assertEqual(result, 0)
        saved_config = save_mock.call_args.args[0]
        self.assertEqual(saved_config.provider, CODEX_PROVIDER)
        self.assertEqual(saved_config.model, "gpt-5.6-sol")
        self.assertIn("默认模型已保存：gpt-5.6-sol", output.getvalue())
        fake_agent.close.assert_called_once()

    @patch("agent_search_1688.cli.save_1688_openai_api_key")
    @patch("agent_search_1688.cli.list_1688_provider_models")
    @patch("agent_search_1688.cli.resolve_1688_purchase_provider")
    @patch("agent_search_1688.cli._prompt_1688_openai_api_key")
    @patch("agent_search_1688.cli.load_1688_purchase_config")
    def test_update_key_is_validated_before_it_is_saved(
        self,
        load_mock,
        prompt_mock,
        resolve_mock,
        list_mock,
        save_mock,
    ) -> None:
        load_mock.return_value = PurchaseConfig(
            provider=CODEX_PROVIDER,
            model="gpt-5.6-sol",
        )
        prompt_mock.return_value = "sk-test-new"
        runtime = ProviderRuntime(
            provider=OPENAI_PROVIDER,
            model="",
            api_mode="openai_responses_sse",
            base_url="https://api.openai.com/v1",
            credential_source="interactive-input",
            credential="sk-test-new",
        )
        resolve_mock.return_value = runtime
        list_mock.return_value = [
            ModelOption("gpt-5", "gpt-5", "test")
        ]
        save_mock.return_value = "credential-file"
        arguments = argparse.Namespace(
            list=False,
            set=None,
            status=False,
            update_key=True,
            delete_key=False,
        )

        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            result = run_1688_provider_command(arguments)

        self.assertEqual(result, 0)
        list_mock.assert_called_once_with(runtime, timeout_seconds=300)
        save_mock.assert_called_once_with("sk-test-new")


if __name__ == "__main__":
    unittest.main()
