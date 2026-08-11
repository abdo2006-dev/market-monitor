#!/usr/bin/env python
"""
Read-only schema state diagnostic.

Classifies a database into one of the cases described in docs/RUNBOOK.md §2.2 so
that the correct remediation can be chosen. This script NEVER writes: no DDL, no
stamp, no migration. It opens a connection, reads catalog metadata, and prints a
report.

    python scripts/check_schema_state.py
    DATABASE_URL=... python scripts/check_schema_state.py --json

Cases:
    A  Managed by Alembic and at head. Nothing to do.
    A- Managed by Alembic but behind head. Run `alembic upgrade head`.
    B  Schema structurally matches the current model, but alembic_version is
       missing. This is what `Base.metadata.create_all` produces. Safe to stamp
       AFTER taking a backup.
    B- Schema is unstamped and matches the Phase 1A head (0003). Back up, then
       stamp exactly 0003 so later migrations are not skipped.
    C  Real drift: tables or columns are missing or unexpected. DO NOT STAMP.
       Requires manual inspection.
    D  Empty database. Run `alembic upgrade head` to create it.

Exit codes: 0 = A, 10 = A-, 20 = B, 21 = B-, 30 = C, 40 = D, 1 = error.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text  # noqa: E402

from app.database import Base, engine  # noqa: E402
import app.models  # noqa: F401,E402  (registers the tables on Base.metadata)


EXIT_AT_HEAD = 0
EXIT_BEHIND = 10
EXIT_UNSTAMPED = 20
EXIT_UNSTAMPED_HISTORICAL = 21
EXIT_DRIFT = 30
EXIT_EMPTY = 40


def _expected_schema() -> dict[str, set[str]]:
    return {
        name: {c.name for c in table.columns}
        for name, table in Base.metadata.tables.items()
    }


def _alembic_head() -> str | None:
    """Read the head revision from the migration scripts, not from the database."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
        cfg.set_main_option(
            "script_location",
            str(Path(__file__).resolve().parent.parent / "alembic"),
        )
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception as exc:  # pragma: no cover - diagnostic fallback
        print(f"  ! could not read migration head: {exc}", file=sys.stderr)
        return None


async def inspect_database() -> dict:
    expected = _expected_schema()

    def _read(sync_conn):
        insp = inspect(sync_conn)
        present = set(insp.get_table_names())
        return {
            name: {c["name"] for c in insp.get_columns(name)}
            for name in present
        }

    async with engine.connect() as conn:
        actual = await conn.run_sync(_read)

        stamped_revision = None
        has_version_table = "alembic_version" in actual
        if has_version_table:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            row = result.first()
            stamped_revision = row[0] if row else None

    app_tables = {t: cols for t, cols in actual.items() if t != "alembic_version"}

    missing_tables = sorted(set(expected) - set(app_tables))
    unexpected_tables = sorted(set(app_tables) - set(expected))

    missing_columns: dict[str, list[str]] = {}
    unexpected_columns: dict[str, list[str]] = {}
    for table in sorted(set(expected) & set(app_tables)):
        missing = sorted(expected[table] - app_tables[table])
        extra = sorted(app_tables[table] - expected[table])
        if missing:
            missing_columns[table] = missing
        if extra:
            unexpected_columns[table] = extra

    head = _alembic_head()
    structurally_matches = not missing_tables and not missing_columns
    phase_1a_missing = missing_columns == {
        "products": ["canonical_url", "identity_key"]
    }
    matches_phase_1a = not missing_tables and phase_1a_missing

    if not app_tables:
        case, action = "D", "Empty database. Run: alembic upgrade head"
    elif has_version_table and stamped_revision:
        if head and stamped_revision != head:
            case = "A-"
            action = f"Behind head ({stamped_revision} -> {head}). Run: alembic upgrade head"
        else:
            case = "A"
            action = "Managed by Alembic and at head. No action required."
    elif structurally_matches:
        case = "B"
        action = (
            "Schema matches the models but alembic_version is missing "
            "(built by create_all). BACK UP FIRST, then: "
            f"alembic stamp {head or '<head>'}"
        )
    elif matches_phase_1a:
        case = "B-"
        action = (
            "Schema matches Phase 1A revision 0003 but is unstamped. BACK UP FIRST, "
            "then run: alembic stamp 0003_reconcile_index_names. Do NOT stamp head; "
            "that would skip the product-integrity migration."
        )
    else:
        case = "C"
        action = (
            "REAL DRIFT DETECTED. Do NOT stamp. "
            "Reconcile manually - see docs/RUNBOOK.md §2.2 Case C."
        )

    # Extra columns alone do not prevent stamping, but they must be reported.
    if case in {"B", "B-"} and unexpected_columns:
        action += (
            "  NOTE: unexpected extra columns were found; review them before stamping."
        )

    return {
        "case": case,
        "action": action,
        "alembic_version_table_present": has_version_table,
        "stamped_revision": stamped_revision,
        "migration_head": head,
        "tables_present": sorted(app_tables),
        "missing_tables": missing_tables,
        "unexpected_tables": unexpected_tables,
        "missing_columns": missing_columns,
        "unexpected_columns": unexpected_columns,
    }


def _print_report(report: dict) -> None:
    print("=" * 68)
    print("  Market Monitor - database schema state (READ ONLY)")
    print("=" * 68)
    print(f"  alembic_version table : {'present' if report['alembic_version_table_present'] else 'ABSENT'}")
    print(f"  stamped revision      : {report['stamped_revision'] or '-'}")
    print(f"  migration head        : {report['migration_head'] or '-'}")
    print(f"  tables present        : {len(report['tables_present'])}")
    for label, key in (
        ("missing tables", "missing_tables"),
        ("unexpected tables", "unexpected_tables"),
    ):
        if report[key]:
            print(f"  {label:22}: {', '.join(report[key])}")
    for label, key in (
        ("missing columns", "missing_columns"),
        ("unexpected columns", "unexpected_columns"),
    ):
        if report[key]:
            print(f"  {label:22}:")
            for table, cols in report[key].items():
                print(f"      {table}: {', '.join(cols)}")
    print("-" * 68)
    print(f"  CASE {report['case']}")
    print(f"  {report['action']}")
    print("=" * 68)
    if report["case"] in {"B", "B-", "C"}:
        print("  Take a backup before any remediation:")
        print("    pg_dump \"$DATABASE_URL\" > backup-$(date +%F-%H%M).sql")
        print("=" * 68)


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args()

    try:
        report = await inspect_database()
    except Exception as exc:
        print(f"ERROR: could not inspect database: {exc}", file=sys.stderr)
        print(
            "Set DATABASE_URL to the database you want to inspect. "
            f"Currently: {os.environ.get('DATABASE_URL', '<unset, using default>')}",
            file=sys.stderr,
        )
        return 1
    finally:
        await engine.dispose()

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_report(report)

    return {
        "A": EXIT_AT_HEAD,
        "A-": EXIT_BEHIND,
        "B": EXIT_UNSTAMPED,
        "B-": EXIT_UNSTAMPED_HISTORICAL,
        "C": EXIT_DRIFT,
        "D": EXIT_EMPTY,
    }[report["case"]]


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
