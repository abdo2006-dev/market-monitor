#!/usr/bin/env python
"""Read-only audit of duplicate logical products and their dependent history."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.database import engine  # noqa: E402
from scripts.product_integrity_common import build_audit, load_product_rows  # noqa: E402


def _json_default(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


async def audit_database() -> dict:
    async with engine.connect() as connection:
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        rows, invalid = await load_product_rows(connection)
        report = build_audit(rows, invalid)
        await connection.rollback()
        return report


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the complete JSON report")
    args = parser.parse_args()
    try:
        report = await audit_database()
    finally:
        await engine.dispose()

    if args.json:
        print(json.dumps(report, indent=2, default=_json_default))
    else:
        print("Market Monitor product duplicate audit (READ ONLY)")
        print(json.dumps(report["summary"], indent=2))
        if report["invalid_identity_rows"]:
            print("Invalid identity rows:")
            print(json.dumps(report["invalid_identity_rows"], indent=2))
        for group in report["logical_duplicate_groups"]:
            print(json.dumps(group, indent=2, default=_json_default))
    return 2 if report["invalid_identity_rows"] else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
