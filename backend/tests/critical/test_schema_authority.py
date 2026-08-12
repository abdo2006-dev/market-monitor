"""
Part B invariant: Alembic is the single schema authority.

These tests protect the fix for ARCHITECTURE A-1. They fail if anyone
reintroduces runtime schema creation or lets the models drift away from the
migrations again.
"""
from __future__ import annotations

import inspect
import os
import subprocess
import sys

import pytest

from tests.conftest import requires_db

pytestmark = pytest.mark.critical


# ── Static invariants (no database required) ──────────────────────────────────

def _ddl_offences(module) -> list[str]:
    """
    Find real schema-mutating code in a module.

    Uses the AST rather than a text search so that prose in docstrings and
    diagnostic messages (which legitimately mention create_all) is not flagged.
    """
    import ast

    tree = ast.parse(inspect.getsource(module))
    offences: list[str] = []

    for node in ast.walk(tree):
        # Base.metadata.create_all(...) / drop_all(...)
        if isinstance(node, ast.Attribute) and node.attr in {"create_all", "drop_all"}:
            offences.append(f"line {node.lineno}: .{node.attr} accessed")

        # text("CREATE TABLE ..."), conn.exec_driver_sql("ALTER TABLE ...")
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name in {"text", "exec_driver_sql", "execute"}:
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        sql = " ".join(arg.value.upper().split())
                        for ddl in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE"):
                            if ddl in sql:
                                offences.append(f"line {node.lineno}: {ddl} in SQL")

    return offences


def test_application_startup_never_creates_schema():
    """
    The app must not call create_all or emit DDL at runtime.

    This is the regression guard for A-1: startup DDL is what produced databases
    with no alembic_version stamp and divergent index names.
    """
    import app.database as database
    import app.main as main

    for module in (database, main):
        offences = _ddl_offences(module)
        assert not offences, (
            f"{module.__name__} mutates schema at runtime: {offences}. "
            "Alembic owns the schema (docs/adr/0002-postgres-source-of-truth.md); "
            "add a migration instead."
        )


def test_ddl_detector_actually_detects_ddl():
    """Guard the guard: a detector that never fires would be worthless."""
    import ast
    import types

    module = types.ModuleType("fake")
    module.__source = "async def go(conn):\n    await conn.execute(text('ALTER TABLE x ADD COLUMN y int'))\n"

    tree = ast.parse(module.__source)
    found = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "ALTER TABLE" in n.value
    ]
    assert found, "sanity check failed"

    # And the real detector must flag the same shape.
    import inspect as _inspect

    class _Fake:
        pass

    original = _inspect.getsource
    try:
        _inspect.getsource = lambda _m: module.__source
        assert _ddl_offences(_Fake) != []
    finally:
        _inspect.getsource = original


def test_startup_hook_calls_verification_not_creation():
    import app.main as main

    source = inspect.getsource(main.startup)
    assert "verify_schema_state" in source
    assert "init_db" not in source


def test_migration_head_is_single():
    """Multiple heads mean a branched history that upgrade head cannot resolve."""
    from app.database import _migration_head

    assert _migration_head() is not None


def test_schema_check_modes_are_known():
    from app.config import settings

    assert settings.DB_SCHEMA_CHECK in {"strict", "warn", "off"}


def test_classifier_error_path_never_prints_database_url():
    """Connection errors must not turn the classifier into a credential oracle."""
    from scripts import check_schema_state

    source = inspect.getsource(check_schema_state._main)
    assert "DATABASE_URL" in source
    assert "os.environ" not in source
    assert "configured value is intentionally not displayed" in source


@requires_db
async def test_classifier_recognizes_unstamped_phase_1a_schema_as_case_b_minus(
    migrated_database,
):
    """The documented 0003 stamp path must remain executable after later migrations."""
    from sqlalchemy import text

    from app.database import engine
    from scripts.check_schema_state import inspect_database

    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    env = {**os.environ, "DATABASE_URL": migrated_database}

    def alembic(*args: str) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=backend_dir,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    alembic("downgrade", "0003_reconcile_index_names")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE alembic_version"))

        report = await inspect_database()

        assert report["case"] == "B-", report
        assert report["stamped_revision"] is None
        assert report["missing_tables"] == ["sync_request_runs", "sync_requests"]
    finally:
        alembic("stamp", "0003_reconcile_index_names")
        alembic("upgrade", "head")


# ── Database-backed invariants ────────────────────────────────────────────────

@requires_db
async def test_migrations_produce_the_model_schema(migrated_database):
    """
    Every table and column the models declare must exist after `upgrade head`.

    This is the check that would have caught the original drift.
    """
    from sqlalchemy import inspect as sa_inspect

    import app.models  # noqa: F401
    from app.database import Base, engine

    def _read(sync_conn):
        insp = sa_inspect(sync_conn)
        return {
            name: {c["name"] for c in insp.get_columns(name)}
            for name in insp.get_table_names()
        }

    async with engine.connect() as conn:
        actual = await conn.run_sync(_read)

    for table_name, table in Base.metadata.tables.items():
        assert table_name in actual, f"migrations did not create table {table_name!r}"
        expected_columns = {c.name for c in table.columns}
        missing = expected_columns - actual[table_name]
        assert not missing, f"{table_name} is missing columns: {sorted(missing)}"


@requires_db
async def test_migrated_database_index_names_match_model_convention(migrated_database):
    """
    Migration 0003 converges the two historical index-naming schemes.

    Before it, an Alembic-built database had ix_snapshots_* while a
    create_all-built one had ix_product_snapshots_*.
    """
    from sqlalchemy import text

    from app.database import engine

    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename IN ('product_snapshots', 'scrape_runs')"
            )
        )
        names = {r[0] for r in rows}

    for expected in (
        "ix_product_snapshots_id",
        "ix_product_snapshots_product_id",
        "ix_product_snapshots_checked_at",
        "ix_scrape_runs_id",
        "ix_scrape_runs_competitor_id",
    ):
        assert expected in names, f"missing index {expected!r}"

    for legacy in ("ix_snapshots_product_id", "ix_snapshots_checked_at"):
        assert legacy not in names, (
            f"legacy index {legacy!r} still present; migration 0003 did not converge"
        )


@requires_db
async def test_verify_schema_state_reports_ok_on_migrated_database(migrated_database):
    from app.database import verify_schema_state

    report = await verify_schema_state()

    assert report["ok"] is True, report["problem"]
    assert report["stamped_revision"] == report["migration_head"]


@requires_db
async def test_verify_schema_state_strict_mode_raises_on_unstamped_database(
    migrated_database, monkeypatch
):
    """
    Startup must fail clearly — not silently mutate — when the schema is not
    under Alembic control.
    """
    from sqlalchemy import text

    from app.config import settings
    from app.database import SchemaStateError, engine, verify_schema_state

    monkeypatch.setattr(settings, "DB_SCHEMA_CHECK", "strict")

    async with engine.begin() as conn:
        stamped = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        await conn.execute(text("DELETE FROM alembic_version"))

    try:
        with pytest.raises(SchemaStateError) as excinfo:
            await verify_schema_state()
        assert "alembic_version" in str(excinfo.value)
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                {"v": stamped},
            )


@requires_db
async def test_verify_schema_state_warn_mode_does_not_raise(
    migrated_database, monkeypatch
):
    """
    The Phase 1A default must not take a running deployment down on boot.
    """
    from sqlalchemy import text

    from app.config import settings
    from app.database import engine, verify_schema_state

    monkeypatch.setattr(settings, "DB_SCHEMA_CHECK", "warn")

    async with engine.begin() as conn:
        stamped = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        await conn.execute(text("DELETE FROM alembic_version"))

    try:
        report = await verify_schema_state()
        assert report["ok"] is False
        assert "alembic_version" in report["problem"]
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                {"v": stamped},
            )
