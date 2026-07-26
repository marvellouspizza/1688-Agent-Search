"""1688 智能采购普通对话的上下文组装。"""

from __future__ import annotations

from .models import Message, ProviderRuntime
from .soul import load_or_seed_1688_soul


STABLE_PURCHASE_INSTRUCTIONS = """\
你只能进行文字回答或调用本项目提供的工具；不得执行命令、修改文件或假设存在未提供的工具。
当用户要求阅读或使用 Skill 时，先调用 skills_list，再调用 skill_view；只使用返回的项目 Skill，不继承本机 Codex Skill。
遇到需要最新公开信息、链接或 1688 候选商家时，使用 web_search；必要时使用 web_extract 或 browser_navigate/browser_snapshot 核验可访问的页面。寻找多家商家时可多次检索并按 URL 去重。
搜索结果只是公开搜索索引：不要把它表述为库存、价格、发票资质或商家身份已经核验。最终回答应提供来源链接；若不足目标数量，必须如实说明。
如果用户询问代码，请用适合编程初学者的方式解释。
"""

class PurchasePromptBuilder:
    def build_1688_purchase_base_instructions(self) -> str:
        return "\n\n".join(
            [
                load_or_seed_1688_soul(),
                STABLE_PURCHASE_INSTRUCTIONS.strip(),
            ]
        )

    def build_1688_purchase_context(
        self,
        *,
        session_id: str,
        provider_runtime: ProviderRuntime,
    ) -> str:
        del session_id, provider_runtime
        return ""

    def count_1688_purchase_context_characters(
        self,
        history: list[Message],
        user_input: str,
    ) -> int:
        return (
            len(STABLE_PURCHASE_INSTRUCTIONS)
            + sum(len(message.content) for message in history)
            + len(user_input)
        )
