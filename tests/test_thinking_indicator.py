from io import StringIO
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch

from agent_search_1688.cli import _ask_1688_purchase_agent
from agent_search_1688.display import HermesThinkingSpinner
from agent_search_1688.models import ChatStatus


class _TTYBuffer(StringIO):
    def isatty(self):
        return True


class _PipeBuffer(StringIO):
    def isatty(self):
        return False


class ThinkingIndicatorTests(unittest.TestCase):
    def test_tty_animation_uses_hermes_frame_timer_and_clear_line(self):
        output = _TTYBuffer()
        spinner = HermesThinkingSpinner(
            "(◔_◔) pondering...",
            spinner_type="dots",
            output=output,
        )

        def stop_after_first_frame(_seconds):
            spinner.running = False

        spinner.running = True
        spinner.start_time = time.time()
        with patch(
            "agent_search_1688.display.time.sleep",
            side_effect=stop_after_first_frame,
        ):
            spinner._animate()
        spinner.stop()

        rendered = output.getvalue()
        self.assertIn("\r  ⠋ (◔_◔) pondering... (0.0s)", rendered)
        self.assertTrue(rendered.endswith("\r"))
        self.assertGreaterEqual(rendered.count(" "), 40)

    def test_non_tty_logs_once_instead_of_repainting_frames(self):
        output = _PipeBuffer()
        spinner = HermesThinkingSpinner(
            "(◔_◔) pondering...",
            spinner_type="dots",
            output=output,
        )

        def stop_after_log(_seconds):
            spinner.running = False

        spinner.running = True
        with patch(
            "agent_search_1688.display.time.sleep",
            side_effect=stop_after_log,
        ):
            spinner._animate()

        self.assertEqual(
            output.getvalue(),
            "  [tool] (◔_◔) pondering...\n",
        )

    def test_cli_stops_status_before_streamed_reply(self):
        spinner = Mock()

        class _Agent:
            def chat(self, _text, *, on_delta, on_thinking):
                on_thinking(True)
                on_delta("搜索完成")
                on_thinking(False)
                return SimpleNamespace(
                    status=ChatStatus.COMPLETED,
                    error=None,
                )

        output = StringIO()
        with patch.object(
            HermesThinkingSpinner,
            "create_for_model_request",
            return_value=spinner,
        ), patch("sys.stdout", output):
            status = _ask_1688_purchase_agent(_Agent(), "搜索")

        self.assertEqual(status, ChatStatus.COMPLETED)
        spinner.start.assert_called_once_with()
        spinner.stop.assert_called_once_with()
        self.assertEqual(output.getvalue(), "1688 Agent > 搜索完成\n")

    def test_cli_restarts_status_for_the_next_model_request(self):
        first_spinner = Mock()
        second_spinner = Mock()

        class _Agent:
            def chat(self, _text, *, on_delta, on_thinking):
                on_thinking(True)
                on_delta("正在调用工具")
                on_thinking(False)
                on_thinking(True)
                on_delta("最终结果")
                on_thinking(False)
                return SimpleNamespace(
                    status=ChatStatus.COMPLETED,
                    error=None,
                )

        output = StringIO()
        with patch.object(
            HermesThinkingSpinner,
            "create_for_model_request",
            side_effect=[first_spinner, second_spinner],
        ), patch("sys.stdout", output):
            _ask_1688_purchase_agent(_Agent(), "搜索")

        first_spinner.stop.assert_called_once_with()
        second_spinner.stop.assert_called_once_with()
        self.assertEqual(
            output.getvalue(),
            "1688 Agent > 正在调用工具\n1688 Agent > 最终结果\n",
        )


if __name__ == "__main__":
    unittest.main()
