"""1688 智能采购普通对话的上下文组装。"""

from __future__ import annotations

from pathlib import Path

from .models import Message, ProviderRuntime
from .skills import SkillCatalog
from .soul import load_or_seed_1688_soul


# Ported from Hermes upstream `agent/prompt_builder.py` at eb527605.  The
# capability-specific passages about Hermes' terminal and file tools are not
# included because this project does not register those tools.
TOOL_USE_ENFORCEMENT_GUIDANCE = """# Tool-use enforcement
You MUST use your tools to take action — do not describe what you would do or plan to do without actually doing it. When you say you will perform an action, you MUST immediately make the corresponding tool call in the same response. Never end your turn with a promise of future action — execute it now.
Keep working until the task is actually complete. Do not stop with a summary of what you plan to do next time. If you have tools available that can accomplish the task, use them instead of telling the user what you would do.
Every response should either (a) contain tool calls that make progress, or (b) deliver a final result to the user. Responses that only describe intentions without acting are not acceptable."""

OPENAI_MODEL_EXECUTION_GUIDANCE = """# Execution discipline
<tool_persistence>
- Use tools whenever they improve correctness, completeness, or grounding.
- Do not stop early when another tool call would materially improve the result.
- If a tool returns empty or partial results, retry with a different query or strategy before giving up.
- Keep calling tools until: (1) the task is complete, AND (2) you have verified the result.
</tool_persistence>

<prerequisite_checks>
- Before taking an action, check whether prerequisite discovery, lookup, or context-gathering steps are needed.
- Do not skip prerequisite steps just because the final action seems obvious.
- If a task depends on output from a prior step, resolve that dependency first.
</prerequisite_checks>

<verification>
Before finalizing your response:
- Correctness: does the output satisfy every stated requirement?
- Grounding: are factual claims backed by tool outputs or provided context?
- Formatting: does the output match the requested format or schema?
</verification>

<missing_context>
- If required context is missing, do NOT guess or hallucinate an answer.
- Use the appropriate registered project tool when missing information is retrievable.
- Ask a clarifying question only when the information cannot be retrieved by tools.
- If you must proceed with incomplete information, label assumptions explicitly.
</missing_context>"""

PROJECT_SCOPE_INSTRUCTIONS = """\
You may answer in text or call only the project tools provided in this request. Do not assume that any other local Codex, Hermes, terminal, file, or browser capability exists.
"""

class PurchasePromptBuilder:
    def __init__(self, skill_root: Path):
        self.skill_catalog = SkillCatalog([skill_root])

    def build_1688_purchase_base_instructions(self) -> str:
        parts = [load_or_seed_1688_soul(), PROJECT_SCOPE_INSTRUCTIONS.strip()]
        # Hermes uses the same model-family gate with `tool_use_enforcement:
        # auto`; gpt-5.6-sol therefore receives both blocks.
        parts.extend((TOOL_USE_ENFORCEMENT_GUIDANCE, OPENAI_MODEL_EXECUTION_GUIDANCE))
        skills_prompt = self.build_1688_skills_system_prompt()
        if skills_prompt:
            parts.append(skills_prompt)
        return "\n\n".join(parts)

    def build_1688_skills_system_prompt(self) -> str:
        entries = self.skill_catalog.list()
        if not entries:
            return ""
        index_lines = [
            f"  project:\n" + "\n".join(
                f"    - {entry.name}: {entry.description}" for entry in entries
            )
        ]
        return (
            "## Skills (mandatory)\n"
            "Before replying, scan the skills below. If a skill matches or is even partially relevant "
            "to your task, you MUST load it with skill_view(name) and follow its instructions. "
            "Err on the side of loading — it is always better to have context you don't need "
            "than to miss critical steps, pitfalls, or established workflows. "
            "Skills contain specialized knowledge and proven workflows that outperform general-purpose approaches. "
            "Load the skill even if you think you could handle the task with basic tools like web_search.\n\n"
            "<available_skills>\n"
            + "\n".join(index_lines)
            + "\n</available_skills>\n\n"
            "Only proceed without loading a skill if genuinely none are relevant to the task."
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
            len(self.build_1688_purchase_base_instructions())
            + sum(len(message.content) for message in history)
            + len(user_input)
        )
