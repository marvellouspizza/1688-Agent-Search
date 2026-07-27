"""1688 智能采购普通对话的核心运行时。"""

from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
from typing import Callable, Protocol

from .config import CODEX_PROVIDER, OPENAI_PROVIDER, PurchaseConfig
from .models import (
    ChatResult,
    ChatStatus,
    ConversationState,
    MessageStatus,
    ProviderRuntime,
    PurchaseSession,
    ProviderTurnResult,
)
from .prompt_builder import PurchasePromptBuilder
from .providers import (
    CodexPurchaseProviderAdapter,
    PurchaseProviderError,
    PurchaseProviderInterrupted,
)
from .session_store import PurchaseSessionStore
from .tools.web.search import build_1688_tool_registry


class PurchaseProviderAdapter(Protocol):
    provider_runtime: ProviderRuntime
    actual_model: str
    thread_id: str | None

    def open_1688_purchase_session(
        self,
        session: PurchaseSession,
        history: list,
    ) -> str: ...

    def stream_1688_model_reply(self, **kwargs: object): ...

    def switch_1688_purchase_model(self, model: str) -> None: ...

    def interrupt_1688_model_reply(self) -> None: ...

    def close(self) -> None: ...


class PurchaseAgentRuntime:
    """CLI、网页等入口以后都只调用这个核心对象。"""

    def __init__(
        self,
        *,
        config: PurchaseConfig,
        provider_runtime: ProviderRuntime,
        session_store: PurchaseSessionStore,
        prompt_builder: PurchasePromptBuilder,
        provider_adapter: PurchaseProviderAdapter,
        cwd: Path,
    ):
        self.config = config
        self.provider_runtime = provider_runtime
        self.session_store = session_store
        self.prompt_builder = prompt_builder
        self.provider_adapter = provider_adapter
        self.session: PurchaseSession | None = None
        self.state = ConversationState.IDLE
        # Use the application root supplied by the entry point.  `Path.cwd()`
        # is the user's shell directory and is not a reliable project root.
        self.tool_registry = build_1688_tool_registry(skill_root=cwd / "skills")

    def create_or_restore_1688_purchase_session(
        self,
        session_id: str | None = None,
    ) -> PurchaseSession:
        if self.state is not ConversationState.IDLE:
            raise RuntimeError("只有 IDLE 状态可以创建或恢复 Session")
        session = self.session_store.create_or_restore_1688_purchase_session(
            session_id,
            self.provider_runtime,
        )
        if session.provider != self.provider_runtime.provider:
            raise ValueError(
                f"Session 属于供应商 {session.provider}，当前供应商是 "
                f"{self.provider_runtime.provider}。请创建新 Session。"
            )
        self.session_store.acquire_1688_session_lock(session.id)
        history = self.session_store.load_1688_purchase_context_messages(session.id)
        provider_thread_id = self.provider_adapter.open_1688_purchase_session(
            session,
            history,
        )
        self.session_store.attach_1688_provider_thread(
            session.id,
            provider_thread_id,
            self.provider_adapter.actual_model,
        )
        self.session = self.session_store.get_1688_purchase_session(session.id)
        return self.session

    def chat(
        self,
        user_input: str,
        session_id: str | None = None,
        *,
        on_delta: Callable[[str], None] | None = None,
    ) -> ChatResult:
        """稳定核心入口：AgentRuntime.chat(user_input, session_id)。"""

        if self.state is not ConversationState.IDLE:
            raise RuntimeError(f"Agent 当前不是空闲状态：{self.state.value}")
        if not user_input.strip():
            raise ValueError("用户输入不能为空")
        if self.session is None:
            self.create_or_restore_1688_purchase_session(session_id)
        elif session_id is not None and session_id != self.session.id:
            raise ValueError("当前 Agent 已绑定另一个 Session")
        assert self.session is not None

        delta_callback = on_delta or (lambda _delta: None)
        partial_parts: list[str] = []
        user_message_id = ""
        request_id = ""
        self._transition_to(ConversationState.PREPARING)

        try:
            history = self.session_store.load_1688_purchase_context_messages(
                self.session.id
            )
            context_size = (
                self.prompt_builder.count_1688_purchase_context_characters(
                    history,
                    user_input,
                )
            )
            if context_size > self.config.max_context_characters:
                raise RuntimeError(
                    "当前会话上下文已超过第一版上限，请新建 Session 后继续"
                )

            user_message, request_id = (
                self.session_store.begin_1688_purchase_request(
                    self.session.id,
                    user_input,
                    self.provider_runtime,
                )
            )
            user_message_id = user_message.id
            self._transition_to(ConversationState.REQUESTING)

            def handle_stream_started() -> None:
                if self.state is ConversationState.REQUESTING:
                    self._transition_to(ConversationState.STREAMING)
                    self.session_store.mark_1688_purchase_request_streaming(
                        request_id
                    )

            def handle_delta(delta: str) -> None:
                partial_parts.append(delta)
                delta_callback(delta)

            if hasattr(self.provider_adapter, "run_1688_model_turn"):
                provider_result = self._run_1688_tool_loop(
                    user_input=user_input,
                    request_id=request_id,
                    on_stream_started=handle_stream_started,
                    on_delta=handle_delta,
                )
            else:
                provider_result = self.provider_adapter.stream_1688_model_reply(
                    user_input=user_input,
                    user_message_id=user_message.id,
                    on_stream_started=handle_stream_started,
                    on_delta=handle_delta,
                )
            if self.state is ConversationState.REQUESTING:
                handle_stream_started()
            assistant = self.session_store.save_1688_purchase_reply(
                session_id=self.session.id,
                request_id=request_id,
                content=provider_result.content,
                provider_runtime=self.provider_runtime,
                actual_model=provider_result.actual_model,
                usage=provider_result.usage,
                provider_thread_id=provider_result.provider_thread_id,
            )
            self.provider_runtime = replace(
                self.provider_runtime,
                model=provider_result.actual_model,
            )
            self.session = self.session_store.get_1688_purchase_session(
                self.session.id
            )
            self._transition_to(ConversationState.COMPLETED)
            result = ChatResult(
                status=ChatStatus.COMPLETED,
                session_id=self.session.id,
                message_id=assistant.id,
                content=assistant.content,
                provider=assistant.provider,
                model=assistant.model,
                usage=provider_result.usage,
                error=None,
            )
        except PurchaseProviderInterrupted as exc:
            self.state = ConversationState.INTERRUPTED
            if request_id and user_message_id:
                self.session_store.fail_1688_purchase_request(
                    request_id=request_id,
                    user_message_id=user_message_id,
                    status=MessageStatus.INTERRUPTED,
                    error=str(exc),
                )
            result = ChatResult(
                status=ChatStatus.INTERRUPTED,
                session_id=self.session.id,
                message_id="",
                content="".join(partial_parts),
                provider=self.provider_runtime.provider,
                model=self.provider_runtime.model,
                error=str(exc),
            )
        except Exception as exc:
            self.state = ConversationState.FAILED
            if request_id and user_message_id:
                self.session_store.fail_1688_purchase_request(
                    request_id=request_id,
                    user_message_id=user_message_id,
                    status=MessageStatus.FAILED,
                    error=str(exc),
                )
            result = ChatResult(
                status=ChatStatus.FAILED,
                session_id=self.session.id,
                message_id="",
                content="".join(partial_parts),
                provider=self.provider_runtime.provider,
                model=self.provider_runtime.model,
                error=str(exc),
            )
        finally:
            self.state = ConversationState.IDLE
        return result

    def _run_1688_tool_loop(
        self,
        *,
        user_input: str,
        request_id: str,
        on_stream_started: Callable[[], None],
        on_delta: Callable[[str], None],
    ) -> ProviderTurnResult:
        """Run project-owned function calls; no Codex native capability is used."""
        runner = getattr(self.provider_adapter, "run_1688_model_turn")
        assert self.session is not None
        input_items: list[dict[str, object]] = [
            {"role": message.role.value, "content": message.content}
            for message in self.session_store.load_1688_purchase_context_messages(
                self.session.id
            )
        ]
        input_items.append({"role": "user", "content": user_input})
        tool_definitions = self.tool_registry.definitions()
        seen_calls: set[tuple[str, str]] = set()
        sequence = 0
        for _iteration in range(self.config.max_iterations):
            latest = runner(
                input_items=input_items,
                tool_definitions=tool_definitions,
                on_stream_started=on_stream_started,
                on_delta=on_delta,
            )
            if not latest.tool_calls:
                return latest
            input_items.extend(latest.response_items)
            for call in latest.tool_calls:
                sequence += 1
                signature = (call.name, repr(sorted(call.arguments.items())))
                if signature in seen_calls:
                    raise PurchaseProviderError("模型重复调用相同工具，已停止")
                seen_calls.add(signature)
            calls_with_sequence = list(zip(range(sequence - len(latest.tool_calls) + 1, sequence + 1), latest.tool_calls))
            if len(calls_with_sequence) > 1 and all(
                self.tool_registry.is_parallel_safe(call.name)
                for _, call in calls_with_sequence
            ):
                with ThreadPoolExecutor(max_workers=len(calls_with_sequence)) as executor:
                    futures = [
                        (number, call, executor.submit(self._dispatch_1688_tool_call, call.name, call.arguments))
                        for number, call in calls_with_sequence
                    ]
                    dispatched = [
                        (number, call, *future.result())
                        for number, call, future in futures
                    ]
            else:
                dispatched = [
                    (number, call, *self._dispatch_1688_tool_call(call.name, call.arguments))
                    for number, call in calls_with_sequence
                ]
            for number, call, output, status, duration_ms in dispatched:
                self.session_store.append_1688_tool_trace(
                    session_id=self.session.id,
                    request_id=request_id,
                    sequence=number,
                    call_id=call.call_id,
                    name=call.name,
                    arguments_json=json.dumps(call.arguments, ensure_ascii=False),
                    result_json=output,
                    status=status,
                    duration_ms=duration_ms,
                )
                input_items.append({"type": "function_call_output", "call_id": call.call_id, "output": output})

        # Hermes gives the model one final, tool-free request after the
        # iteration budget is exhausted so a long research turn still returns
        # the evidence collected so far instead of failing the whole request.
        input_items.append(
            {
                "role": "user",
                "content": (
                    "You've reached the maximum number of tool-calling "
                    "iterations allowed. Please provide a final response "
                    "summarizing what you've found and accomplished so far, "
                    "without calling any more tools."
                ),
            }
        )
        try:
            for _attempt in range(2):
                summary = runner(
                    input_items=input_items,
                    tool_definitions=[],
                    on_stream_started=on_stream_started,
                    on_delta=on_delta,
                )
                if not summary.tool_calls and summary.content.strip():
                    return summary
            fallback = (
                "I reached the iteration limit and couldn't generate a "
                "summary."
            )
        except Exception as exc:
            fallback = (
                f"I reached the maximum iterations "
                f"({self.config.max_iterations}) but couldn't summarize. "
                f"Error: {str(exc)[:1_000]}"
            )
        on_delta(fallback)
        return replace(
            latest,
            content=fallback,
            tool_calls=[],
            response_items=[],
        )

    def _dispatch_1688_tool_call(
        self,
        name: str,
        arguments: dict,
    ) -> tuple[str, str, int]:
        started = time.monotonic()
        try:
            result = self.tool_registry.dispatch(name, arguments)
            return (
                json.dumps(result, ensure_ascii=False)[:30_000],
                "completed",
                round((time.monotonic() - started) * 1000),
            )
        except Exception as exc:
            return (
                json.dumps({"error": str(exc)[:1_000]}, ensure_ascii=False),
                "failed",
                round((time.monotonic() - started) * 1000),
            )

    def switch_1688_purchase_model(self, model: str) -> None:
        if self.state is not ConversationState.IDLE:
            raise RuntimeError("模型回复期间不能切换模型")
        if not model.strip():
            raise ValueError("模型名称不能为空")
        self.provider_adapter.switch_1688_purchase_model(model)
        self.provider_runtime = self.provider_adapter.provider_runtime
        if self.session is not None and self.provider_adapter.thread_id is not None:
            self.session_store.attach_1688_provider_thread(
                self.session.id,
                self.provider_adapter.thread_id,
                model,
            )
            self.session = self.session_store.get_1688_purchase_session(
                self.session.id
            )

    def stop_1688_purchase_reply(self) -> None:
        self.provider_adapter.interrupt_1688_model_reply()

    def close(self) -> None:
        try:
            self.provider_adapter.close()
        finally:
            self.session_store.close()

    def _transition_to(self, next_state: ConversationState) -> None:
        allowed: dict[ConversationState, set[ConversationState]] = {
            ConversationState.IDLE: {ConversationState.PREPARING},
            ConversationState.PREPARING: {
                ConversationState.REQUESTING,
                ConversationState.FAILED,
            },
            ConversationState.REQUESTING: {
                ConversationState.STREAMING,
                ConversationState.FAILED,
            },
            ConversationState.STREAMING: {
                ConversationState.COMPLETED,
                ConversationState.FAILED,
                ConversationState.INTERRUPTED,
                ConversationState.INCOMPLETE,
            },
            ConversationState.COMPLETED: {ConversationState.IDLE},
            ConversationState.FAILED: {ConversationState.IDLE},
            ConversationState.INTERRUPTED: {ConversationState.IDLE},
            ConversationState.INCOMPLETE: {ConversationState.IDLE},
        }
        if next_state not in allowed[self.state]:
            raise RuntimeError(
                f"非法状态变化：{self.state.value} → {next_state.value}"
            )
        self.state = next_state


def create_1688_purchase_agent(
    *,
    config: PurchaseConfig,
    provider_runtime: ProviderRuntime,
    session_store: PurchaseSessionStore,
    cwd: Path,
) -> PurchaseAgentRuntime:
    prompt_builder = PurchasePromptBuilder(cwd.resolve() / "skills")
    if provider_runtime.provider == CODEX_PROVIDER:
        from .providers import CodexResponsesProviderAdapter

        provider_adapter: PurchaseProviderAdapter = CodexResponsesProviderAdapter(
            provider_runtime,
            config,
            prompt_builder,
        )
    elif provider_runtime.provider == OPENAI_PROVIDER:
        from .providers import OpenAIResponsesProviderAdapter

        provider_adapter = OpenAIResponsesProviderAdapter(
            provider_runtime,
            config,
            prompt_builder,
        )
    else:
        raise PurchaseProviderError(
            f"不支持的模型供应商：{provider_runtime.provider}"
        )
    return PurchaseAgentRuntime(
        config=config,
        provider_runtime=provider_runtime,
        session_store=session_store,
        prompt_builder=prompt_builder,
        provider_adapter=provider_adapter,
        cwd=cwd.resolve(),
    )
