"""SQLAlchemy engine, session factory and the declarative Base."""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


def _normalize_db_url(url: str) -> str:
    """Managed Postgres providers (Render, Heroku, Supabase) hand out
    `postgres://` / `postgresql://` URLs. SQLAlchemy + psycopg3 needs the
    explicit `postgresql+psycopg://` driver prefix.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


DATABASE_URL = _normalize_db_url(settings.database_url)

# `check_same_thread` is only needed for SQLite; harmless to compute conditionally.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Base class for all ORM models."""


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables. For real projects prefer Alembic migrations."""
    # Import models so they are registered on the Base metadata before create_all.
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
