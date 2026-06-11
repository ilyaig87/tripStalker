"""Guard against the bug class that started this work: an ORM column added to a
model but NOT to the `_ADDED_COLUMNS` lightweight migration, so existing DBs
(local SQLite, live Postgres) never get the column and writes crash.
"""
from sqlalchemy import inspect

from app import models
from app.database import _ADDED_COLUMNS, engine, init_db

# Columns present at the very first release (the spec's original schema). Anything
# added later MUST appear in _ADDED_COLUMNS so existing DBs get patched on startup.
_BASELINE = {
    "users": {"id", "email", "created_at"},
    "tracked_items": {
        "id", "user_id", "provider", "raw_url", "destination", "check_in_date",
        "check_out_date", "room_config", "target_hotel_id_or_name",
        "initial_price", "current_price", "currency", "status", "created_at",
    },
    "price_history": {"id", "tracked_item_id", "price", "checked_at"},
}

_TABLES = {
    "users": models.User,
    "tracked_items": models.TrackedItem,
    "price_history": models.PriceHistory,
}


def test_every_new_column_is_covered_by_migration():
    for name, model in _TABLES.items():
        model_cols = {c.name for c in model.__table__.columns}
        migrated = set(_ADDED_COLUMNS.get(name, {}))
        uncovered = model_cols - _BASELINE[name] - migrated
        assert not uncovered, (
            f"{name}: columns missing from _ADDED_COLUMNS (existing DBs would crash): {uncovered}"
        )


def test_no_stale_migration_entries():
    for name, cols in _ADDED_COLUMNS.items():
        model_cols = {c.name for c in _TABLES[name].__table__.columns}
        stale = set(cols) - model_cols
        assert not stale, f"{name}: migration lists columns not on the model: {stale}"


def test_fresh_db_has_all_model_columns():
    init_db()
    insp = inspect(engine)
    for name, model in _TABLES.items():
        db_cols = {c["name"] for c in insp.get_columns(name)}
        model_cols = {c.name for c in model.__table__.columns}
        assert model_cols <= db_cols, f"{name}: DB missing {model_cols - db_cols}"
