"""Pcode catalog. The default list lives in config/laws.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from twbc_rag.paths import DEFAULT_CONFIG


@dataclass(frozen=True)
class LawConfig:
    pcode: str
    doc_type: str
    aliases: list[str] = field(default_factory=list)
    parent_act_pcode: str | None = None
    parent_act_name: str | None = None


@dataclass(frozen=True)
class Catalog:
    laws: list[LawConfig]

    @property
    def pcodes(self) -> list[str]:
        return [law.pcode for law in self.laws]

    def by_pcode(self, pcode: str) -> LawConfig | None:
        for law in self.laws:
            if law.pcode == pcode:
                return law
        return None

    def name_index(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Map citation aliases and official titles onto pcodes. Longer names win later ties."""
        index: dict[str, str] = {}
        for law in self.laws:
            for alias in law.aliases:
                cleaned = alias.strip()
                if cleaned:
                    index[cleaned] = law.pcode
        if extra:
            for name, pcode in extra.items():
                cleaned = name.strip()
                if cleaned:
                    index[cleaned] = pcode
        return index


def load_catalog(path: Path | None = None) -> Catalog:
    config_path = path or DEFAULT_CONFIG
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    laws: list[LawConfig] = []
    for item in raw.get("laws") or []:
        pcode = str(item["pcode"]).strip().upper()
        laws.append(
            LawConfig(
                pcode=pcode,
                doc_type=str(item.get("doc_type") or "law"),
                aliases=[str(alias) for alias in (item.get("aliases") or [])],
                parent_act_pcode=(
                    str(item["parent_act_pcode"]).strip().upper()
                    if item.get("parent_act_pcode")
                    else None
                ),
                parent_act_name=(
                    str(item["parent_act_name"]).strip() if item.get("parent_act_name") else None
                ),
            )
        )
    return Catalog(laws=laws)


def fallback_config(pcode: str, *, name: str, law_level: str) -> LawConfig:
    """Used when a pcode is requested but not listed in the YAML catalog."""
    doc_type = "law" if law_level in {"憲法", "法律"} else "order"
    aliases = [name] if name else []
    return LawConfig(pcode=pcode, doc_type=doc_type, aliases=aliases)
