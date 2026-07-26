import json
import unittest

from agent_search_1688.mcp_server import handle_mcp_message
from agent_search_1688.searxng import parse_searxng_results, validate_searxng_base_url
from agent_search_1688.web_search_tool import build_1688_tool_registry


class SearXNGToolsTests(unittest.TestCase):
    def setUp(self):
        self.registry = build_1688_tool_registry()

    def test_mcp_lists_only_web_search(self):
        response = handle_mcp_message(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            self.registry,
        )
        self.assertIsNotNone(response)
        tools = response["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], ["web_search"])

    def test_mcp_returns_structured_unknown_tool_error(self):
        response = handle_mcp_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "shell", "arguments": {}},
            },
            self.registry,
        )
        self.assertTrue(response["result"]["isError"])
        self.assertIn("未注册工具", response["result"]["content"][0]["text"])

    def test_result_parser_deduplicates_and_limits(self):
        results = parse_searxng_results(
            {
                "results": [
                    {"url": "https://detail.1688.com/a", "title": "A", "content": "one"},
                    {"url": "https://detail.1688.com/a", "title": "duplicate"},
                    {"url": "https://detail.1688.com/b", "title": "B"},
                ]
            },
            2,
        )
        self.assertEqual([item["url"] for item in results], [
            "https://detail.1688.com/a",
            "https://detail.1688.com/b",
        ])

    def test_rejects_non_base_searxng_url(self):
        with self.assertRaises(ValueError):
            validate_searxng_base_url("http://127.0.0.1:8888/search?q=x")

    def test_tool_schema_rejects_extra_arguments(self):
        with self.assertRaises(ValueError):
            self.registry.dispatch("web_search", {"query": "1688", "url": "x"})

    def test_mcp_tool_result_is_json_text(self):
        response = handle_mcp_message(
            {"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {}},
            self.registry,
        )
        self.assertEqual(response["result"]["serverInfo"]["name"], "1688-tools")
        self.assertEqual(json.dumps(response["result"]), json.dumps(response["result"]))


if __name__ == "__main__":
    unittest.main()
