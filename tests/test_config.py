from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_search_1688.config import (
    APP_HOME_ENV,
    MODEL_ENV,
    OPENAI_PROVIDER,
    PROVIDER_ENV,
    PurchaseConfig,
    PurchaseConfigError,
    load_1688_purchase_config,
    save_1688_purchase_config,
)
from agent_search_1688.provider import resolve_1688_purchase_provider


class PurchaseConfigTests(unittest.TestCase):
    @patch("agent_search_1688.provider.shutil.which", return_value=None)
    def test_openai_provider_does_not_require_codex(self, which_mock) -> None:
        runtime = resolve_1688_purchase_provider(
            PurchaseConfig(provider=OPENAI_PROVIDER, model="gpt-5.6"),
            environ={"OPENAI_API_KEY": "sk-test-openai"},
        )
        self.assertEqual(runtime.provider, OPENAI_PROVIDER)
        self.assertEqual(runtime.model, "gpt-5.6")
        self.assertEqual(runtime.credential_source, "environment:OPENAI_API_KEY")
        self.assertEqual(runtime.credential, "sk-test-openai")
        self.assertNotIn("sk-test-openai", repr(runtime))
        which_mock.assert_not_called()

    def test_save_and_load_non_secret_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {APP_HOME_ENV: directory}, clear=False):
                saved_path = save_1688_purchase_config(
                    PurchaseConfig(
                        provider="local-codex-chatgpt",
                        model="gpt-5.6-terra",
                    )
                )
                loaded = load_1688_purchase_config()

            self.assertEqual(loaded.model, "gpt-5.6-terra")
            self.assertEqual(loaded.provider, "local-codex-chatgpt")
            payload = json.loads(saved_path.read_text(encoding="utf-8"))
            self.assertNotIn("token", payload)
            self.assertNotIn("api_key", payload)

    def test_broken_config_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("{broken", encoding="utf-8")
            with patch.dict(os.environ, {APP_HOME_ENV: directory}, clear=False):
                with self.assertRaises(PurchaseConfigError):
                    load_1688_purchase_config()

    def test_wrong_config_types_are_rejected_cleanly(self) -> None:
        invalid_values = [
            {"provider": 123},
            {"model": ["gpt-test"]},
            {"database_path": False},
            {"request_timeout_seconds": "300"},
            {"max_context_characters": True},
        ]
        for payload in invalid_values:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with patch.dict(
                    os.environ,
                    {APP_HOME_ENV: directory},
                    clear=False,
                ):
                    with self.assertRaises(PurchaseConfigError):
                        load_1688_purchase_config()

    @patch("agent_search_1688.provider.shutil.which", return_value="/bin/codex")
    @patch("agent_search_1688.provider.subprocess.run")
    def test_provider_resolution_priority(
        self,
        run_mock,
        _which_mock,
    ) -> None:
        def command_result(command, **_kwargs):
            if "--version" in command:
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout="codex-cli 0.144.4",
                    stderr="",
                )
            if "features" in command:
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=(
                        "shell_tool stable true\n"
                        "personality stable true\n"
                    ),
                    stderr="",
                )
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="Logged in using ChatGPT",
                stderr="",
            )

        run_mock.side_effect = command_result
        config = PurchaseConfig(
            provider="local-codex-chatgpt",
            model="config-model",
        )
        environment = {
            PROVIDER_ENV: "environment-provider",
            MODEL_ENV: "environment-model",
        }

        from_config = resolve_1688_purchase_provider(
            config,
            environ=environment,
        )
        from_cli = resolve_1688_purchase_provider(
            config,
            cli_model="cli-model",
            cli_provider="local-codex-chatgpt",
            environ=environment,
        )

        self.assertEqual(from_config.model, "config-model")
        self.assertEqual(from_cli.model, "cli-model")

    @patch("agent_search_1688.provider.shutil.which", return_value="/bin/codex")
    @patch("agent_search_1688.provider.subprocess.run")
    def test_api_key_login_is_rejected(
        self,
        run_mock,
        _which_mock,
    ) -> None:
        def command_result(command, **_kwargs):
            if "--version" in command:
                stdout = "codex-cli 0.144.4"
            elif "features" in command:
                stdout = "shell_tool stable true\n"
            else:
                stdout = "Logged in using an API key"
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=stdout,
                stderr="",
            )

        run_mock.side_effect = command_result
        with self.assertRaisesRegex(Exception, "不是 ChatGPT 登录"):
            resolve_1688_purchase_provider(PurchaseConfig())


if __name__ == "__main__":
    unittest.main()
