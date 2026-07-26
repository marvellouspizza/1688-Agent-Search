from __future__ import annotations

import unittest

from agent_search_1688.models import (
    Message,
    MessageRole,
    MessageStatus,
    validate_1688_conversation_roles,
)


def message(role: MessageRole, number: int) -> Message:
    return Message(
        id=f"msg_{number}",
        session_id="session_test",
        role=role,
        content=str(number),
        status=MessageStatus.COMPLETED,
        provider="local-codex-chatgpt",
        model="test-model",
        created_at=f"2026-07-25T00:00:0{number}+08:00",
    )


class MessageRoleTests(unittest.TestCase):
    def test_valid_user_assistant_order(self) -> None:
        validate_1688_conversation_roles(
            [
                message(MessageRole.USER, 1),
                message(MessageRole.ASSISTANT, 2),
                message(MessageRole.USER, 3),
                message(MessageRole.ASSISTANT, 4),
            ]
        )

    def test_two_users_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_1688_conversation_roles(
                [
                    message(MessageRole.USER, 1),
                    message(MessageRole.USER, 2),
                ]
            )

    def test_dangling_user_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_1688_conversation_roles(
                [message(MessageRole.USER, 1)]
            )


if __name__ == "__main__":
    unittest.main()
