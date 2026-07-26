"""普通对话路线使用的统一内部数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class MessageStatus(str, Enum):
    PENDING = "pending"
    STREAMING = "streaming"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    INCOMPLETE = "incomplete"


class ChatStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    INCOMPLETE = "incomplete"


class ConversationState(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    REQUESTING = "requesting"
    STREAMING = "streaming"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class Message:
    id: str
    session_id: str
    role: MessageRole
    content: str
    status: MessageStatus
    provider: str
    model: str
    created_at: str


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_codex(cls, value: dict[str, Any] | None) -> "TokenUsage":
        if not value:
            return cls()
        return cls(
            input_tokens=int(value.get("inputTokens", 0) or 0),
            output_tokens=int(value.get("outputTokens", 0) or 0),
            total_tokens=int(value.get("totalTokens", 0) or 0),
        )


@dataclass(frozen=True)
class ChatResult:
    status: ChatStatus
    session_id: str
    message_id: str
    content: str
    provider: str
    model: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    error: str | None = None


@dataclass(frozen=True)
class ProviderRuntime:
    provider: str
    model: str
    api_mode: str
    base_url: str
    credential_source: str
    codex_path: str | None = None
    credential: str | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class ModelOption:
    model: str
    display_name: str
    description: str
    is_default: bool = False
    hidden: bool = False


@dataclass(frozen=True)
class PurchaseSession:
    id: str
    provider: str
    model: str
    provider_thread_id: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ProviderStreamResult:
    content: str
    usage: TokenUsage
    actual_model: str
    provider_thread_id: str


@dataclass(frozen=True)
class ProviderToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ProviderTurnResult:
    content: str
    tool_calls: list[ProviderToolCall]
    response_items: list[dict[str, Any]]
    usage: TokenUsage
    actual_model: str
    response_id: str


def validate_1688_conversation_roles(messages: list[Message]) -> None:
    """发送模型前固定检查 User/Assistant 交替顺序。"""

    expected = MessageRole.USER
    for message in messages:
        if message.status is not MessageStatus.COMPLETED:
            continue
        if message.role is not expected:
            raise ValueError(
                f"会话角色顺序错误：期望 {expected.value}，实际 {message.role.value}"
            )
        expected = (
            MessageRole.ASSISTANT
            if expected is MessageRole.USER
            else MessageRole.USER
        )

    if messages and expected is MessageRole.ASSISTANT:
        raise ValueError("会话中存在尚未配对的用户消息")
