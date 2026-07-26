from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_search_1688.soul import DEFAULT_SOUL, load_or_seed_1688_soul


class GlobalSoulTests(unittest.TestCase):
    def test_missing_global_soul_is_seeded_once(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch("agent_search_1688.soul.get_1688_purchase_home", return_value=home):
                content = load_or_seed_1688_soul()
                path = home / "SOUL.md"
                self.assertEqual(content, DEFAULT_SOUL.strip())
                self.assertTrue(path.exists())

    def test_existing_global_soul_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / "SOUL.md"
            path.write_text("自定义身份", encoding="utf-8")
            with patch("agent_search_1688.soul.get_1688_purchase_home", return_value=home):
                self.assertEqual(load_or_seed_1688_soul(), "自定义身份")
                self.assertEqual(path.read_text(encoding="utf-8"), "自定义身份")


if __name__ == "__main__":
    unittest.main()
