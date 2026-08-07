from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .models import Base, Provider, utcnow


def make_engine(database_path: Path):
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    return engine


def make_session_factory(settings: Settings):
    return sessionmaker(bind=make_engine(settings.database_path), expire_on_commit=False, class_=Session)


def init_db(session_factory, settings: Settings) -> None:
    settings.ensure_directories()
    engine = session_factory.kw["bind"]
    Base.metadata.create_all(engine)
    with session_factory() as session:
        provider = session.get(Provider, settings.image_provider_id)
        if provider is None:
            provider = Provider(
                id=settings.image_provider_id,
                name=settings.image_provider_name,
                kind="openai-async",
                base_url=settings.image_base_url,
                api_key_env="CODEX_IMAGE_API_KEY",
                config_json=json.dumps({"model": settings.image_model}, ensure_ascii=False),
            )
            session.add(provider)
        else:
            changed = False
            if settings.image_base_url and provider.base_url != settings.image_base_url:
                provider.base_url = settings.image_base_url
                changed = True
            if settings.image_provider_name and provider.name != settings.image_provider_name:
                provider.name = settings.image_provider_name
                changed = True
            if changed:
                provider.updated_at = utcnow()
        session.commit()


@contextmanager
def session_scope(session_factory) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def enable_fts(engine) -> None:
    """Create a small FTS table when SQLite supports it; search code has a fallback."""
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS asset_search USING fts5(
                    asset_id UNINDEXED,
                    content,
                    tokenize='unicode61'
                )
                """
            )
        )
