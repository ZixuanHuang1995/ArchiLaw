"""Shared records passed from a source adapter into the database loader."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class RawAttachment:
    file_name: str
    file_url: str


@dataclass
class RawArticle:
    article_type: str
    article_no: str
    content: str
    # None: derive from preceding ArticleType "C" rows. A string (possibly empty)
    # is already resolved, which is how the Hugging Face fallback arrives.
    heading_path: str | None = None


@dataclass
class RawLaw:
    pcode: str
    name: str
    law_level: str
    official_url: str
    category: str
    modified_date: date
    modified_date_raw: str
    effective_date: date | None
    effective_date_raw: str
    effective_note: str
    histories: str
    foreword: str
    attachments: list[RawAttachment] = field(default_factory=list)
    articles: list[RawArticle] = field(default_factory=list)
    source: str = "moj_api"
    source_update_date: str | None = None


class SourceError(RuntimeError):
    """A remote corpus (MOJ or Hugging Face) could not be read."""
