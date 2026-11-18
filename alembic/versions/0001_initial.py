"""Initial building-code corpus schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "law",
        sa.Column("pcode", sa.String(length=16), primary_key=True),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("doc_type", sa.String(length=32), nullable=False),
        sa.Column("law_level", sa.String(length=32), nullable=True),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column("official_url", sa.Text(), nullable=False),
        sa.Column("parent_act_pcode", sa.String(length=16), nullable=True),
        sa.Column("parent_act_name", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(["parent_act_pcode"], ["law.pcode"], ondelete="SET NULL"),
    )
    op.create_table(
        "law_version",
        sa.Column("version_id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("pcode", sa.String(length=16), nullable=False),
        sa.Column("modified_date", sa.Date(), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column(
            "effective_date_raw",
            sa.String(length=32),
            nullable=True,
            comment="Original MOJ string. 99991231 means there is no single effective date.",
        ),
        sa.Column("effective_note", sa.Text(), nullable=True),
        sa.Column("histories", sa.Text(), nullable=True),
        sa.Column("foreword", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_update_date", sa.String(length=64), nullable=True),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["pcode"], ["law.pcode"], ondelete="CASCADE"),
        sa.UniqueConstraint("pcode", "modified_date", name="uq_law_version_date"),
    )
    op.create_index("ix_law_version_pcode", "law_version", ["pcode"])
    op.create_table(
        "article",
        sa.Column("article_id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("article_no", sa.String(length=64), nullable=False),
        sa.Column("article_no_main", sa.Integer(), nullable=False),
        sa.Column("article_no_sub", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("heading_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("full_text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["law_version.version_id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "version_id", "article_no_main", "article_no_sub", name="uq_article_number"
        ),
    )
    op.create_index("ix_article_version_id", "article", ["version_id"])
    op.create_table(
        "clause",
        sa.Column("clause_id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column(
            "clause_path",
            sa.String(length=64),
            nullable=False,
            comment="項/款/目 as slash-separated integers, e.g. 1/2/3. A 但書 reuses that path.",
        ),
        sa.Column("is_proviso", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["article.article_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("article_id", "seq", name="uq_clause_seq"),
    )
    op.create_index("ix_clause_article_id", "clause", ["article_id"])
    op.create_table(
        "cross_ref",
        sa.Column("cross_ref_id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("from_clause_id", sa.Integer(), nullable=False),
        sa.Column("to_article_id", sa.Integer(), nullable=True),
        sa.Column("to_clause_id", sa.Integer(), nullable=True),
        sa.Column("raw_span", sa.Text(), nullable=False),
        sa.Column("target_law_name", sa.String(length=512), nullable=True),
        sa.Column("span_start", sa.Integer(), nullable=True),
        sa.Column("span_end", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["from_clause_id"], ["clause.clause_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_article_id"], ["article.article_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["to_clause_id"], ["clause.clause_id"], ondelete="SET NULL"),
    )
    op.create_index("ix_cross_ref_from_clause_id", "cross_ref", ["from_clause_id"])
    op.create_index("ix_cross_ref_to_article_id", "cross_ref", ["to_article_id"])
    op.create_index("ix_cross_ref_to_clause_id", "cross_ref", ["to_clause_id"])
    op.create_table(
        "figure_table",
        sa.Column("asset_id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=True),
        sa.Column("media_type", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=True),
        sa.Column("storage_uri", sa.Text(), nullable=True),
        sa.Column("caption_or_ocr", sa.Text(), nullable=True),
        sa.Column("source_kind", sa.String(length=32), nullable=False, server_default="api"),
        sa.ForeignKeyConstraint(["version_id"], ["law_version.version_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["article_id"], ["article.article_id"], ondelete="SET NULL"),
        sa.UniqueConstraint("version_id", "source_url", name="uq_asset_url"),
    )
    op.create_index("ix_figure_table_version_id", "figure_table", ["version_id"])
    op.create_index("ix_figure_table_article_id", "figure_table", ["article_id"])
    op.create_table(
        "interpretation",
        sa.Column("interpretation_id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("agency", sa.String(length=255), nullable=True),
        sa.Column("doc_no", sa.String(length=128), nullable=True),
        sa.Column("published_date", sa.Date(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
    )
    op.create_table(
        "interpretation_article",
        sa.Column("interpretation_id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["interpretation_id"],
            ["interpretation.interpretation_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["article_id"], ["article.article_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("interpretation_id", "article_id"),
    )


def downgrade() -> None:
    op.drop_table("interpretation_article")
    op.drop_table("interpretation")
    op.drop_table("figure_table")
    op.drop_table("cross_ref")
    op.drop_table("clause")
    op.drop_table("article")
    op.drop_table("law_version")
    op.drop_table("law")
