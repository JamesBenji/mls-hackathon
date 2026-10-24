"""Database engine, session, declarative base, and portable column types.

Postgres is the target (JSONB / ARRAY). The same models also run on SQLite
(tests, offline demos) because the JSON-ish columns use variants.
"""
from collections.abc import Generator

from sqlalchemy import JSON, String, create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings

# JSONB on Postgres, JSON elsewhere.
JSONType = JSON().with_variant(JSONB(), "postgresql")
# text[] on Postgres, JSON list elsewhere. Python side is always list[str].
StrList = JSON().with_variant(ARRAY(String), "postgresql")


def make_engine(url: str):
    if url.startswith("sqlite"):
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url or url.rstrip("/") in ("sqlite:", "sqlite+pysqlite:"):
            # One shared connection so every session sees the same in-memory db.
            kwargs["poolclass"] = StaticPool
        return create_engine(url, future=True, **kwargs)
    return create_engine(url, future=True, pool_pre_ping=True)


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False,
                            expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
