import json
import unittest
from unittest.mock import patch

from agent_search_1688.searxng import search_searxng


class _Response:
    def __init__(self):
        self.payload = json.dumps({"results": []}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _size):
        return self.payload


class SearXNGLanguageTests(unittest.TestCase):
    def test_search_preserves_server_default_language(self):
        with patch("urllib.request.urlopen", return_value=_Response()) as open_url:
            search_searxng(
                base_url="http://127.0.0.1:8888",
                query="1688 桥牌",
                limit=5,
                timeout_seconds=5,
            )
        request_url = open_url.call_args.args[0].full_url
        self.assertNotIn("language=", request_url)
        self.assertIn("format=json", request_url)


if __name__ == "__main__":
    unittest.main()
