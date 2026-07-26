"""1688 智能采购普通对话的上下文组装。"""

from __future__ import annotations

from importlib import resources

from .config import PurchaseConfig, get_1688_purchase_home
from .models import Message, ProviderRuntime


STABLE_PURCHASE_INSTRUCTIONS = """\
你只能进行文字回答，或调用唯一允许的 web_search 工具；不得执行命令、修改文件或调用未提供的工具。
遇到需要最新公开信息、链接或 1688 候选商家时，使用 web_search。寻找多家商家时可多次检索并按 URL 去重。
搜索结果只是公开搜索索引：不要把它表述为库存、价格、发票资质或商家身份已经核验。最终回答应提供来源链接；若不足目标数量，必须如实说明。
如果用户询问代码，请用适合编程初学者的方式解释。
"""

class PurchasePromptBuilder:
    def __init__(self, config: PurchaseConfig):
        self.config = config

    def build_1688_purchase_base_instructions(self) -> str:
        return "\n\n".join(
            [
                self._load_1688_soul(),
                STABLE_PURCHASE_INSTRUCTIONS.strip(),
            ]
        )

    def _load_1688_soul(self) -> str:
        profile = self.config.soul_profile
        custom_path = get_1688_purchase_home() / "souls" / f"{profile}.md"
        if custom_path.is_file():
            return custom_path.read_text(encoding="utf-8").strip()
        try:
            return (
                resources.files("agent_search_1688.souls")
                .joinpath(f"{profile}.md")
                .read_text(encoding="utf-8")
                .strip()
            )
        except FileNotFoundError as exc:
            raise ValueError(f"未找到 SOUL Profile：{profile}") from exc

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
            len(self.build_1688_purchase_base_instructions())
            + sum(len(message.content) for message in history)
            + len(user_input)
        )
