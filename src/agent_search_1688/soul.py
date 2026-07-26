"""Hermes 风格的单一全局 SOUL.md。"""

from __future__ import annotations

from .config import get_1688_purchase_home


DEFAULT_SOUL = """# Identity
你协助用户进行采购调研与供应商信息整理。

# Defaults
面对采购需求时，优先确认规格、数量、单位和交付限制；需要公开链接或候选商家时使用可用搜索工具。
明确区分搜索候选信息与已核验的库存、价格、发票资质和商家身份，不确定时如实说明。
"""


def get_1688_soul_path():
    return get_1688_purchase_home() / "SOUL.md"


def load_or_seed_1688_soul() -> str:
    path = get_1688_soul_path()
    if not path.exists():
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(DEFAULT_SOUL, encoding="utf-8")
        path.chmod(0o600)
    return path.read_text(encoding="utf-8").strip()
