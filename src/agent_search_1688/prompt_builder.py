"""1688 智能采购普通对话的上下文组装。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .models import Message, ProviderRuntime


STABLE_PURCHASE_INSTRUCTIONS = """\
你是“1688 智能采购”项目的对话助手。
当前阶段只进行普通文字对话：理解用户输入并给出清楚、真实、直接的文字回答。
不要调用工具，不要执行命令，不要修改文件，也不要声称已经完成任何外部操作。
如果用户询问代码，请用适合编程初学者的方式解释。
"""

PURCHASE_PROJECT_CONTEXT = """\
项目名称：1688 Agent Search
目标：逐步构建一个面向 1688 智能采购场景的 Agent。
当前能力边界：普通文本对话、模型切换、会话保存与恢复。
"""


class PurchasePromptBuilder:
    def build_1688_purchase_base_instructions(self) -> str:
        return STABLE_PURCHASE_INSTRUCTIONS.strip()

    def build_1688_purchase_context(
        self,
        *,
        session_id: str,
        provider_runtime: ProviderRuntime,
    ) -> str:
        now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
        parts = [
            PURCHASE_PROJECT_CONTEXT.strip(),
            "",
            "当前运行信息：",
            f"- 时间：{now}",
            f"- Session：{session_id}",
            f"- Provider：{provider_runtime.provider}",
            f"- 模型：{provider_runtime.model}",
        ]
        return "\n".join(parts)

    def count_1688_purchase_context_characters(
        self,
        history: list[Message],
        user_input: str,
    ) -> int:
        return (
            len(STABLE_PURCHASE_INSTRUCTIONS)
            + len(PURCHASE_PROJECT_CONTEXT)
            + sum(len(message.content) for message in history)
            + len(user_input)
        )
