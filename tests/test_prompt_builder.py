import unittest

from agent_search_1688.models import ProviderRuntime
from agent_search_1688.prompt_builder import PurchasePromptBuilder


class PromptBuilderTests(unittest.TestCase):
    def test_does_not_inject_an_unrequested_assistant_persona(self):
        instructions = PurchasePromptBuilder().build_1688_purchase_base_instructions()
        self.assertNotIn("你是", instructions)
        self.assertNotIn("1688 智能采购", instructions)

    def test_does_not_inject_runtime_metadata(self):
        context = PurchasePromptBuilder().build_1688_purchase_context(
            session_id="session_private",
            provider_runtime=ProviderRuntime("provider", "model", "mode", "url", "credential"),
        )
        self.assertEqual(context, "")


if __name__ == "__main__":
    unittest.main()
