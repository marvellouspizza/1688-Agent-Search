import unittest

from agent_search_1688.providers import (
    CodexPurchaseProviderAdapter,
    OpenAIResponsesProviderAdapter,
    PurchaseProviderError,
)
from agent_search_1688.providers.codex import list_1688_provider_models
from agent_search_1688.providers.openai import list_1688_openai_models


class ProvidersPackageTests(unittest.TestCase):
    def test_public_provider_exports_remain_available(self):
        self.assertEqual(CodexPurchaseProviderAdapter.__name__, "CodexPurchaseProviderAdapter")
        self.assertEqual(OpenAIResponsesProviderAdapter.__name__, "OpenAIResponsesProviderAdapter")
        self.assertTrue(issubclass(PurchaseProviderError, RuntimeError))

    def test_provider_specific_modules_expose_their_catalog_functions(self):
        self.assertTrue(callable(list_1688_provider_models))
        self.assertTrue(callable(list_1688_openai_models))


if __name__ == "__main__":
    unittest.main()
