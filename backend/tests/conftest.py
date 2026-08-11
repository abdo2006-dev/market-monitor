"""
Shared pytest fixtures.

Database-backed tests require an explicit TEST_DATABASE_URL. When it is not set
they are SKIPPED, loudly, rather than silently passing — see docs/TESTING.md §5.

    TEST_DATABASE_URL=postgresql+asyncpg://market:market@localhost:55440/market_monitor_test \
        .venv/bin/python -m pytest tests/ -q

Isolation strategy: the schema is created once per session by running the real
Alembic migrations (which also proves migrations work), and every test starts
from truncated tables. Truncation is used rather than a rolled-back outer
transaction because production code paths such as `_scrape_competitor_async`
open their own sessions via `AsyncSessionLocal` and would not join it.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

# Point the application at the test database BEFORE any app module is imported,
# because app.config.Settings and app.database.engine are built at import time.
if TEST_DATABASE_URL:
    if "test" not in TEST_DATABASE_URL.rsplit("/", 1)[-1]:
        raise RuntimeError(
            "Refusing to run: TEST_DATABASE_URL database name must contain 'test'. "
            f"Got: {TEST_DATABASE_URL.rsplit('/', 1)[-1]!r}. "
            "This guard exists so the suite cannot truncate a real database."
        )
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

requires_db = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set; database-backed tests skipped",
)

# Tables truncated between tests, children first.
_TABLES = [
    "events",
    "product_snapshots",
    "products",
    "scrape_runs",
    "competitors",
    "app_settings",
]


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def migrated_database():
    """Create the schema once per session using the real migrations."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")

    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    for args in (["downgrade", "base"], ["upgrade", "head"]):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.fail(
                f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
            )
    return TEST_DATABASE_URL


@pytest.fixture
async def db_session(migrated_database):
    """A clean session against truncated tables."""
    from sqlalchemy import text

    from app.database import AsyncSessionLocal, engine

    async with engine.begin() as conn:
        await conn.execute(
            text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE")
        )

    async with AsyncSessionLocal() as session:
        yield session


@pytest.fixture
async def api_client(db_session):
    """httpx client bound to the ASGI app, sharing the test database."""
    import httpx

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
