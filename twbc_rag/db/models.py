"""SQLAlchemy models.

The shape follows the Week 1 ERD, with a few columns added so the loader can
round-trip MOJ data without dropping fields the later milestones need.
See the README "Data model" section for the reasons.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from twbc_rag.db.base import Base


class Law(Base):
    __tablename__ = "law"

    pcode: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False)
    law_level: Mapped[str | None] = mapped_column(String(32))
    category: Mapped[str | None] = mapped_column(String(255))
    official_url: Mapped[str] = mapped_column(Text, nullable=False)
    parent_act_pcode: Mapped[str | None] = mapped_column(
        ForeignKey("law.pcode", ondelete="SET NULL")
    )
    parent_act_name: Mapped[str | None] = mapped_column(String(512))

    versions: Mapped[list[LawVersion]] = relationship(back_populates="law")


class LawVersion(Base):
    __tablename__ = "law_version"
    __table_args__ = (UniqueConstraint("pcode", "modified_date", name="uq_law_version_date"),)

    version_id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    pcode: Mapped[str] = mapped_column(ForeignKey("law.pcode", ondelete="CASCADE"), nullable=False)
    modified_date: Mapped[date] = mapped_column(Date, nullable=False)
    effective_date: Mapped[date | None] = mapped_column(Date)
    # MOJ writes 99991231 when there is no single effective date (部分條文另定).
    effective_date_raw: Mapped[str | None] = mapped_column(String(32))
    effective_note: Mapped[str | None] = mapped_column(Text)
    histories: Mapped[str | None] = mapped_column(Text)
    foreword: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_update_date: Mapped[str | None] = mapped_column(String(64))
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    law: Mapped[Law] = relationship(back_populates="versions")
    articles: Mapped[list[Article]] = relationship(
        back_populates="version",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    assets: Mapped[list[FigureTable]] = relationship(
        back_populates="version",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Article(Base):
    __tablename__ = "article"
    __table_args__ = (
        UniqueConstraint(
            "version_id",
            "article_no_main",
            "article_no_sub",
            name="uq_article_number",
        ),
    )

    article_id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("law_version.version_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    article_no: Mapped[str] = mapped_column(String(64), nullable=False)
    # 第 110-1 條 -> main 110, sub 1. Plain 第 11 條 -> sub 0.
    article_no_main: Mapped[int] = mapped_column(Integer, nullable=False)
    article_no_sub: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    heading_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    full_text: Mapped[str] = mapped_column(Text, nullable=False)

    version: Mapped[LawVersion] = relationship(back_populates="articles")
    clauses: Mapped[list[Clause]] = relationship(
        back_populates="article",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Clause.seq",
    )
    assets: Mapped[list[FigureTable]] = relationship(back_populates="article")


class Clause(Base):
    __tablename__ = "clause"
    __table_args__ = (UniqueConstraint("article_id", "seq", name="uq_clause_seq"),)

    clause_id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("article.article_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    # Slash-separated 項/款/目 integers: "1", "1/2", "1/2/3".
    # A 但書 reuses its parent path and sets is_proviso.
    clause_path: Mapped[str] = mapped_column(String(64), nullable=False)
    is_proviso: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    article: Mapped[Article] = relationship(back_populates="clauses")
    outgoing_refs: Mapped[list[CrossRef]] = relationship(
        back_populates="source_clause",
        foreign_keys="CrossRef.from_clause_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class CrossRef(Base):
    __tablename__ = "cross_ref"

    cross_ref_id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    from_clause_id: Mapped[int] = mapped_column(
        ForeignKey("clause.clause_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    to_article_id: Mapped[int | None] = mapped_column(
        ForeignKey("article.article_id", ondelete="SET NULL"),
        index=True,
    )
    # 前項 / 前款 / 第N款 point at a clause. Article-level cites leave this null.
    to_clause_id: Mapped[int | None] = mapped_column(
        ForeignKey("clause.clause_id", ondelete="SET NULL"),
        index=True,
    )
    raw_span: Mapped[str] = mapped_column(Text, nullable=False)
    target_law_name: Mapped[str | None] = mapped_column(String(512))
    span_start: Mapped[int | None] = mapped_column(Integer)
    span_end: Mapped[int | None] = mapped_column(Integer)

    source_clause: Mapped[Clause] = relationship(
        back_populates="outgoing_refs",
        foreign_keys=[from_clause_id],
    )
    target_article: Mapped[Article | None] = relationship(foreign_keys=[to_article_id])
    target_clause: Mapped[Clause | None] = relationship(foreign_keys=[to_clause_id])


class FigureTable(Base):
    __tablename__ = "figure_table"
    __table_args__ = (UniqueConstraint("version_id", "source_url", name="uq_asset_url"),)

    asset_id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    # Law-level files (no 第N條 in the filename) still belong to a version.
    version_id: Mapped[int] = mapped_column(
        ForeignKey("law_version.version_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("article.article_id", ondelete="SET NULL"),
        index=True,
    )
    media_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str | None] = mapped_column(Text)
    storage_uri: Mapped[str | None] = mapped_column(Text)
    caption_or_ocr: Mapped[str | None] = mapped_column(Text)
    # api, page, or api+page. Page scrape adds the "layout incomplete" warning.
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="api")

    version: Mapped[LawVersion] = relationship(back_populates="assets")
    article: Mapped[Article | None] = relationship(back_populates="assets")


class Interpretation(Base):
    """Empty until Week 2 (國土署 / 營建署函釋)."""

    __tablename__ = "interpretation"

    interpretation_id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    agency: Mapped[str | None] = mapped_column(String(255))
    doc_no: Mapped[str | None] = mapped_column(String(128))
    published_date: Mapped[date | None] = mapped_column(Date)
    title: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)

    articles: Mapped[list[InterpretationArticle]] = relationship(
        back_populates="interpretation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class InterpretationArticle(Base):
    __tablename__ = "interpretation_article"

    interpretation_id: Mapped[int] = mapped_column(
        ForeignKey("interpretation.interpretation_id", ondelete="CASCADE"),
        primary_key=True,
    )
    article_id: Mapped[int] = mapped_column(
        ForeignKey("article.article_id", ondelete="CASCADE"),
        primary_key=True,
    )

    interpretation: Mapped[Interpretation] = relationship(back_populates="articles")
    article: Mapped[Article] = relationship()
