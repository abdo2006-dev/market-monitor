#!/usr/bin/env bash
# Development bootstrap.
#
# Since Phase 1A the application no longer creates schema at startup — Alembic is
# the single schema authority (docs/adr/0002-postgres-source-of-truth.md). Use
# this to bring a *development* database up to head.
#
#   ./scripts/bootstrap_dev_db.sh
#   DATABASE_URL=postgresql+asyncpg://... ./scripts/bootstrap_dev_db.sh
#
# Do NOT point this at production. For production use docs/RUNBOOK.md §2.2,
# which starts by inspecting the database rather than migrating it.
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
  PYTHON="python3"
fi

echo "==> Inspecting current schema state (read-only)"
set +e
"$PYTHON" scripts/check_schema_state.py
STATE=$?
set -e

case "$STATE" in
  0)  echo "==> Already at head. Nothing to do."; exit 0 ;;
  40) echo "==> Empty database. Creating schema from migrations." ;;
  10) echo "==> Behind head. Upgrading." ;;
  20)
      echo ""
      echo "!! This database has tables but no Alembic stamp (Case B)."
      echo "!! This script will NOT stamp it for you. See docs/RUNBOOK.md §2.2."
      exit 20
      ;;
  30)
      echo ""
      echo "!! Real schema drift detected (Case C). Refusing to migrate."
      echo "!! See docs/RUNBOOK.md §2.2 Case C."
      exit 30
      ;;
  *)  echo "!! Could not determine schema state. Aborting."; exit 1 ;;
esac

echo "==> alembic upgrade head"
"$PYTHON" -m alembic upgrade head

echo "==> Verifying"
"$PYTHON" scripts/check_schema_state.py
