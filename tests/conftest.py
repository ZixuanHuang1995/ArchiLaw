"""Pytest session against the compose database ``twbc_test``."""

from __future__ import annotations

import os

# Override before twbc_rag.db.session.load_dotenv() can keep a developer URL.
os.environ["DATABASE_URL"] = "postgresql+psycopg://twbc:twbc@127.0.0.1:55432/twbc_test"

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from twbc_rag.paths import REPO_ROOT

_MIGRATED = False


@pytest.fixture
def session() -> Iterator[Session]:
    global _MIGRATED
    engine = create_engine(os.environ["DATABASE_URL"])
    if not _MIGRATED:
        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        command.upgrade(config, "head")
        _MIGRATED = True
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE interpretation, law RESTART IDENTITY CASCADE"))
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    try:
        yield db
    finally:
        db.rollback()
        db.close()
        engine.dispose()
