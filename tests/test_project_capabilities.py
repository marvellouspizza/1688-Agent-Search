from pathlib import Path
import tempfile
import unittest

from agent_search_1688.skills import SkillCatalog
from agent_search_1688.tools.web.extract import _validate_public_url


class ProjectCapabilitiesTests(unittest.TestCase):
    def test_copied_1688_skills_are_project_catalog_entries(self):
        root = Path(__file__).parents[1] / "skills"
        catalog = SkillCatalog([root])
        self.assertEqual(
            [entry.name for entry in catalog.list()],
            [
                "1688-find-product-links",
                "1688-identify-product-keywords",
                "1688-product-identification-search",
            ],
        )
        self.assertIn("1688 Find Product", catalog.read("1688-find-product-links"))

    def test_skill_reference_cannot_escape_skill_root(self):
        root = Path(__file__).parents[1] / "skills"
        with self.assertRaises(ValueError):
            SkillCatalog([root]).read("1688-find-product-links", "../../AGENTS.md")

    def test_web_extract_rejects_local_network_targets(self):
        for url in ("http://localhost:8888", "http://127.0.0.1", "http://[::1]"):
            with self.assertRaises(ValueError):
                _validate_public_url(url)


if __name__ == "__main__":
    unittest.main()
