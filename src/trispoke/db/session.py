"""DB engine + session factory.

DATABASE_URL takes priority. If unset, falls back to a SQLite file at
DATABASE_PATH (used by bare-metal dev and the existing tests).

Postgres URLs get a pre-pinged QueuePool with sane production defaults
(pool_size + max_overflow + pool_recycle). SQLite skips pooling entirely
(NullPool + check_same_thread=False), and applies WAL/foreign_keys
pragmas for file-backed databases (skipped for :memory:).
"""

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from trispoke.config import get_settings


_settings = get_settings()


def _resolved_url() -> str:
    if _settings.database_url:
        return _settings.database_url
    return f"sqlite:///{_settings.database_path}"


def _make_engine():
    url = _resolved_url()
    if url.startswith("sqlite"):
        return create_engine(
            url,
            echo=False,
            poolclass=NullPool,
            connect_args={"check_same_thread": False},
        )
    return create_engine(
        url,
        echo=False,
        pool_size=_settings.db_pool_size,
        max_overflow=_settings.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=_settings.db_pool_recycle_seconds,
    )


engine = _make_engine()

# SQLite-only file pragmas (skip in-memory, skip Postgres).
_url = _resolved_url()
if _url.startswith("sqlite") and ":memory:" not in _url:
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.commit()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@contextmanager
def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
