from pathlib import Path
import tempfile
import unittest

from agent_search_1688.skills import SkillCatalog
from agent_search_1688.tools.web.extract import _validate_public_url
from agent_search_1688.config import PurchaseConfig
from agent_search_1688.models import ProviderRuntime
from agent_search_1688.prompt_builder import STABLE_PURCHASE_INSTRUCTIONS
from agent_search_1688.runtime import create_1688_purchase_agent
from agent_search_1688.session_store import PurchaseSessionStore


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

    def test_default_tool_budget_covers_a_skill_research_sequence(self):
        self.assertEqual(PurchaseConfig().max_tool_rounds, 10)
        self.assertIn("绝不可声称", STABLE_PURCHASE_INSTRUCTIONS)

    def test_runtime_uses_supplied_project_root_for_skills(self):
        project_root = Path(__file__).parents[1]
        runtime = ProviderRuntime(
            "local-codex-chatgpt", "gpt-test", "codex_responses",
            "https://example.invalid", "test",
        )
        with tempfile.TemporaryDirectory() as directory:
            agent = create_1688_purchase_agent(
                config=PurchaseConfig(),
                provider_runtime=runtime,
                session_store=PurchaseSessionStore(Path(directory) / "sessions.db"),
                cwd=project_root,
            )
            try:
                result = agent.tool_registry.dispatch("skills_list", {})
                self.assertIn("1688-identify-product-keywords", [
                    entry["name"] for entry in result["skills"]
                ])
            finally:
                agent.close()


if __name__ == "__main__":
    unittest.main()
