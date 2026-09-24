"""Database session management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradelab.config import get_settings

_engine: Engine | None = None


def engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)
    return _engine


@contextmanager
def session() -> Iterator[Session]:
    factory = sessionmaker(bind=engine(), expire_on_commit=False)
    with factory() as s:
        yield s
