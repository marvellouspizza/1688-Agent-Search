from dataclasses import replace
import unittest

from agent_search_1688.config import PurchaseConfig
from agent_search_1688.prompt_builder import PurchasePromptBuilder


class SoulProfileTests(unittest.TestCase):
    def test_procurement_profile_is_the_default(self):
        prompt = PurchasePromptBuilder(PurchaseConfig()).build_1688_purchase_base_instructions()
        self.assertIn("采购调研", prompt)

    def test_general_profile_replaces_procurement_profile(self):
        config = replace(PurchaseConfig(), soul_profile="general")
        prompt = PurchasePromptBuilder(config).build_1688_purchase_base_instructions()
        self.assertIn("通用助手", prompt)
        self.assertNotIn("采购调研", prompt)

    def test_invalid_profile_name_is_rejected_by_config(self):
        from agent_search_1688.config import PurchaseConfigError, _validate_1688_purchase_config

        with self.assertRaises(PurchaseConfigError):
            _validate_1688_purchase_config({"soul_profile": "../other"})


if __name__ == "__main__":
    unittest.main()
