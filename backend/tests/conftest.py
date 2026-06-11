"""Shared test setup: an isolated temp SQLite DB + FastAPI TestClient.

Env vars must be set BEFORE app.config is imported (settings is cached), so we
do it at module top, before importing anything from `app`.
"""
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(suffix=".db")
os.environ["JWT_SECRET"] = "test-secret"
# Ensure comparison/notification side-channels stay offline during tests.
os.environ["TRAVELPAYOUTS_TOKEN"] = ""
os.environ["UNSPLASH_ACCESS_KEY"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _setup_db():
    init_db()
    yield


@pytest.fixture
def client():
    return TestClient(app)
