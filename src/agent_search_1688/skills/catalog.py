"""Contained `SKILL.md` discovery and read-only access."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class SkillEntry:
    name: str
    description: str
    root: Path


class SkillCatalog:
    def __init__(self, roots: list[Path]):
        self.roots = [root.resolve() for root in roots]

    def list(self) -> list[SkillEntry]:
        entries: dict[str, SkillEntry] = {}
        for root in self.roots:
            if not root.is_dir():
                continue
            for skill_file in root.glob("*/SKILL.md"):
                entry = self._entry(skill_file)
                if entry.name in entries:
                    raise ValueError(f"Skill 名称重复：{entry.name}")
                entries[entry.name] = entry
        return sorted(entries.values(), key=lambda entry: entry.name)

    def read(self, name: str, relative_path: str | None = None) -> str:
        entry = next((item for item in self.list() if item.name == name), None)
        if entry is None:
            raise KeyError(f"未找到项目 Skill：{name}")
        candidate = entry.root / (relative_path or "SKILL.md")
        resolved = candidate.resolve()
        if entry.root not in resolved.parents and resolved != entry.root:
            raise ValueError("Skill 引用文件超出 Skill 目录")
        if not resolved.is_file():
            raise FileNotFoundError("Skill 文件不存在")
        if resolved.stat().st_size > 200_000:
            raise ValueError("Skill 文件过大")
        return resolved.read_text(encoding="utf-8")

    @staticmethod
    def _entry(skill_file: Path) -> SkillEntry:
        text = skill_file.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            raise ValueError(f"Skill 缺少 YAML frontmatter：{skill_file}")
        frontmatter = text.split("---\n", 2)[1]
        values = dict(re.findall(r"^(name|description):\s*(.+)$", frontmatter, flags=re.MULTILINE))
        name = values.get("name", "").strip()
        description = values.get("description", "").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name) or not description:
            raise ValueError(f"Skill frontmatter 无效：{skill_file}")
        return SkillEntry(name=name, description=description, root=skill_file.parent.resolve())
