from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_search_1688.config import APP_HOME_ENV
from agent_search_1688.credentials import (
    OPENAI_API_KEY_ENV,
    delete_1688_openai_api_key,
    load_1688_openai_api_key,
    save_1688_openai_api_key,
)


class PurchaseCredentialTests(unittest.TestCase):
    def test_environment_key_has_priority(self) -> None:
        key, source = load_1688_openai_api_key(
            {OPENAI_API_KEY_ENV: "sk-test-environment"}
        )
        self.assertEqual(key, "sk-test-environment")
        self.assertEqual(source, "environment:OPENAI_API_KEY")

    def test_fallback_credential_file_is_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {APP_HOME_ENV: directory, OPENAI_API_KEY_ENV: ""},
                clear=False,
            ):
                with patch("agent_search_1688.credentials.platform.system", return_value="Linux"):
                    source = save_1688_openai_api_key("sk-test-private")
                    key, loaded_source = load_1688_openai_api_key()

            path = Path(directory) / "credentials.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(source, "credential-file")
            self.assertEqual(loaded_source, "credential-file")
            self.assertEqual(key, "sk-test-private")
            self.assertEqual(payload["openai_api_key"], "sk-test-private")
            self.assertEqual(mode, 0o600)

    def test_insecure_credential_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            path.write_text(
                json.dumps({"openai_api_key": "sk-test-insecure"}),
                encoding="utf-8",
            )
            path.chmod(0o644)
            with patch.dict(
                os.environ,
                {APP_HOME_ENV: directory, OPENAI_API_KEY_ENV: ""},
                clear=False,
            ):
                with patch("agent_search_1688.credentials.platform.system", return_value="Linux"):
                    with self.assertRaisesRegex(Exception, "权限过宽"):
                        load_1688_openai_api_key()

    def test_saved_credential_can_be_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {APP_HOME_ENV: directory, OPENAI_API_KEY_ENV: ""},
                clear=False,
            ):
                with patch(
                    "agent_search_1688.credentials.platform.system",
                    return_value="Linux",
                ):
                    save_1688_openai_api_key("sk-test-delete")
                    removed = delete_1688_openai_api_key()
                    key, source = load_1688_openai_api_key()

            self.assertEqual(removed, ["credential-file"])
            self.assertIsNone(key)
            self.assertEqual(source, "not-configured")
            self.assertFalse((Path(directory) / "credentials.json").exists())

    def test_keychain_update_failure_does_not_create_fallback_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failed = subprocess.CompletedProcess(
                args=["security"],
                returncode=1,
                stdout="",
                stderr="User interaction is not allowed",
            )
            with patch.dict(
                os.environ,
                {APP_HOME_ENV: directory, OPENAI_API_KEY_ENV: ""},
                clear=False,
            ):
                with patch(
                    "agent_search_1688.credentials.platform.system",
                    return_value="Darwin",
                ), patch(
                    "agent_search_1688.credentials.shutil.which",
                    return_value="/usr/bin/security",
                ), patch(
                    "agent_search_1688.credentials.subprocess.run",
                    return_value=failed,
                ):
                    with self.assertRaisesRegex(Exception, "钥匙串拒绝保存"):
                        save_1688_openai_api_key("sk-test-new")

            self.assertFalse((Path(directory) / "credentials.json").exists())

    def test_keychain_delete_failure_is_reported(self) -> None:
        failed = subprocess.CompletedProcess(
            args=["security"],
            returncode=1,
            stdout="",
            stderr="User interaction is not allowed",
        )
        with patch(
            "agent_search_1688.credentials.platform.system",
            return_value="Darwin",
        ), patch(
            "agent_search_1688.credentials.shutil.which",
            return_value="/usr/bin/security",
        ), patch(
            "agent_search_1688.credentials.subprocess.run",
            return_value=failed,
        ):
            with self.assertRaisesRegex(Exception, "钥匙串拒绝删除"):
                delete_1688_openai_api_key()

    def test_missing_keychain_item_still_deletes_fallback_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            not_found = subprocess.CompletedProcess(
                args=["security"],
                returncode=44,
                stdout="",
                stderr="The specified item could not be found in the keychain.",
            )
            with patch.dict(
                os.environ,
                {APP_HOME_ENV: directory, OPENAI_API_KEY_ENV: ""},
                clear=False,
            ):
                with patch(
                    "agent_search_1688.credentials.platform.system",
                    return_value="Linux",
                ):
                    save_1688_openai_api_key("sk-test-fallback")
                with patch(
                    "agent_search_1688.credentials.platform.system",
                    return_value="Darwin",
                ), patch(
                    "agent_search_1688.credentials.shutil.which",
                    return_value="/usr/bin/security",
                ), patch(
                    "agent_search_1688.credentials.subprocess.run",
                    return_value=not_found,
                ):
                    removed = delete_1688_openai_api_key()

            self.assertEqual(removed, ["credential-file"])
            self.assertFalse((Path(directory) / "credentials.json").exists())


if __name__ == "__main__":
    unittest.main()
