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


# Columns added after the first release, per table. We add them on startup so
# existing databases (local SQLite, the live Neon Postgres) pick them up without
# Alembic.
_NUM = {"sqlite": "NUMERIC(12,2)", "postgresql": "NUMERIC(12,2)"}
_ADDED_COLUMNS = {
    "users": {
        "password_hash": {"sqlite": "VARCHAR(255)", "postgresql": "VARCHAR(255)"},
    },
    "tracked_items": {
        "hotel_name": {"sqlite": "VARCHAR(255)", "postgresql": "VARCHAR(255)"},
        "hotel_url": {"sqlite": "VARCHAR(500)", "postgresql": "VARCHAR(500)"},
        "destination_city": {"sqlite": "VARCHAR(120)", "postgresql": "VARCHAR(120)"},
        "destination_photo_url": {"sqlite": "VARCHAR(500)", "postgresql": "VARCHAR(500)"},
        "available": {"sqlite": "BOOLEAN DEFAULT 1", "postgresql": "BOOLEAN DEFAULT TRUE"},
        "failed_checks": {"sqlite": "INTEGER DEFAULT 0", "postgresql": "INTEGER DEFAULT 0"},
        "last_error": {"sqlite": "VARCHAR(500)", "postgresql": "VARCHAR(500)"},
        "last_checked_at": {"sqlite": "TIMESTAMP", "postgresql": "TIMESTAMPTZ"},
        "alt_price": _NUM,
        "alt_check_in": {"sqlite": "DATE", "postgresql": "DATE"},
        "alt_check_out": {"sqlite": "DATE", "postgresql": "DATE"},
        "alt_url": {"sqlite": "VARCHAR(1000)", "postgresql": "VARCHAR(1000)"},
        "alt_details": {"sqlite": "VARCHAR(500)", "postgresql": "VARCHAR(500)"},
        "hotel_portion": _NUM,
        "flight_portion": _NUM,
        "flight_details": {"sqlite": "VARCHAR(600)", "postgresql": "VARCHAR(600)"},
    },
    "price_history": {
        "hotel_portion": _NUM,
        "flight_portion": _NUM,
    },
}


def _ensure_columns() -> None:
    """Lightweight idempotent migration: ADD COLUMN for anything missing."""
    from sqlalchemy import text

    dialect = engine.dialect.name  # 'sqlite' or 'postgresql'
    with engine.begin() as conn:
        for table, cols in _ADDED_COLUMNS.items():
            if dialect == "sqlite":
                existing = {r[1] for r in conn.execute(text(f"PRAGMA table_info({table})"))}
            else:
                existing = {
                    r[0]
                    for r in conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = :t"
                        ),
                        {"t": table},
                    )
                }
            for col, ddl in cols.items():
                if col not in existing and dialect in ddl:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl[dialect]}"))


def init_db() -> None:
    """Create all tables, then patch in any newer columns."""
    # Import models so they are registered on the Base metadata before create_all.
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_columns()
