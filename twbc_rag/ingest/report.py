"""Run summary returned by the loader and printed by the CLI."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LawRunStatus:
    pcode: str
    name: str
    status: str
    detail: str = ""


@dataclass
class LawStats:
    pcode: str
    name: str
    modified_date: str | None
    effective_date: str | None
    effective_note: str
    source: str
    articles: int
    clauses: int
    provisos: int
    cross_refs_resolved: int
    cross_refs_unresolved: int
    attachments: int
    deleted_articles: int
    table_articles: int
    layout_warnings: int

    def as_dict(self) -> dict[str, object]:
        return {
            "pcode": self.pcode,
            "name": self.name,
            "modified_date": self.modified_date,
            "effective_date": self.effective_date,
            "effective_note": self.effective_note,
            "source": self.source,
            "articles": self.articles,
            "clauses": self.clauses,
            "provisos": self.provisos,
            "cross_refs_resolved": self.cross_refs_resolved,
            "cross_refs_unresolved": self.cross_refs_unresolved,
            "attachments": self.attachments,
            "deleted_articles": self.deleted_articles,
            "table_articles": self.table_articles,
            "layout_warnings": self.layout_warnings,
        }


@dataclass
class IngestReport:
    results: list[LawRunStatus] = field(default_factory=list)
    stats: list[LawStats] = field(default_factory=list)
    missing_pcodes: list[str] = field(default_factory=list)
    source_notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def changed_pcodes(self) -> list[str]:
        return [item.pcode for item in self.results if item.status in {"inserted", "forced"}]
