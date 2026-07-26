import unittest

from agent_search_1688.tools import build_1688_tool_registry
from agent_search_1688.tools.mcp_server import handle_mcp_message
from agent_search_1688.tools.web.searxng import parse_searxng_results


class ToolsPackageTests(unittest.TestCase):
    def test_registry_is_exposed_from_tools_package(self):
        registry = build_1688_tool_registry()
        self.assertEqual([item["name"] for item in registry.definitions()], ["web_search"])

    def test_mcp_server_uses_the_package_registry(self):
        response = handle_mcp_message(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            build_1688_tool_registry(),
        )
        self.assertEqual(response["result"]["tools"][0]["name"], "web_search")

    def test_web_backend_module_is_importable(self):
        self.assertEqual(parse_searxng_results({"results": []}, 1), [])


if __name__ == "__main__":
    unittest.main()
