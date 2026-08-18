"""Static release gates for the secret-bearing durable Sync workflow."""

from pathlib import Path
import re

import yaml


WORKFLOW_DIR = Path(__file__).parents[2] / ".github" / "workflows"
SYNC_WORKFLOW = WORKFLOW_DIR / "sync-v2.yml"
MIGRATION_WORKFLOW = WORKFLOW_DIR / "db-migrate.yml"
CI_WORKFLOW = WORKFLOW_DIR / "ci.yml"
COVERAGE_WORKFLOW = WORKFLOW_DIR / "competitor-coverage-smoke.yml"


def _workflow(path: Path = SYNC_WORKFLOW) -> dict:
    document = yaml.safe_load(path.read_text())
    # PyYAML's YAML 1.1 loader treats the GitHub key ``on`` as boolean true.
    document["on"] = document.get("on", document.get(True))
    return document


def test_sync_workflow_uses_cairo_local_recovery_schedules():
    schedules = _workflow()["on"]["schedule"]
    assert schedules == [
        {"cron": "17 7 * * *", "timezone": "Africa/Cairo"},
        {"cron": "47 8 * * *", "timezone": "Africa/Cairo"},
    ]


def test_sync_workflow_has_only_safe_triggers_and_trusted_code():
    workflow = _workflow()
    triggers = workflow["on"]
    assert set(triggers) == {"workflow_dispatch", "schedule"}
    assert set(triggers["workflow_dispatch"]["inputs"]) == {"request_id"}
    assert "pull_request" not in triggers
    assert "pull_request_target" not in triggers

    job = workflow["jobs"]["sync"]
    assert workflow["permissions"] == {"contents": "read"}
    assert job["environment"] == "production-sync"
    assert "github.ref == 'refs/heads/main'" in job["if"]
    assert "vars.SYNC_MORNING_ENABLED == 'true'" in job["if"]
    assert job["env"]["SYNC_MORNING_ENABLED"] == (
        "${{ vars.SYNC_MORNING_ENABLED || 'false' }}"
    )
    checkout = job["steps"][0]
    assert checkout["with"]["ref"] == "main"
    assert checkout["with"]["persist-credentials"] is False
    assert re.fullmatch(r"actions/checkout@[0-9a-f]{40}", checkout["uses"])
    setup_python = job["steps"][1]
    assert re.fullmatch(
        r"actions/setup-python@[0-9a-f]{40}", setup_python["uses"]
    )
    assert not any(key in job for key in ("permissions", "continue-on-error"))


def test_migration_workflow_is_manual_environment_scoped_and_trusted_main_only():
    workflow = _workflow(MIGRATION_WORKFLOW)
    triggers = workflow["on"]
    assert set(triggers) == {"workflow_dispatch"}
    assert set(triggers["workflow_dispatch"]["inputs"]) == {
        "action",
        "confirmation",
    }
    assert workflow["permissions"] == {"contents": "read"}

    job = workflow["jobs"]["migrate"]
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert job["environment"] == "production-sync"
    assert job["env"]["DATABASE_URL"] == "${{ secrets.PRODUCTION_DATABASE_URL }}"

    checkout, setup_python = job["steps"][:2]
    assert checkout["with"] == {"ref": "main", "persist-credentials": False}
    assert re.fullmatch(r"actions/checkout@[0-9a-f]{40}", checkout["uses"])
    assert re.fullmatch(
        r"actions/setup-python@[0-9a-f]{40}", setup_python["uses"]
    )


def test_ci_workflow_has_minimum_permissions_and_pinned_actions():
    workflow = _workflow(CI_WORKFLOW)
    assert workflow["permissions"] == {"contents": "read"}

    for job_name, setup_action in (
        ("backend", "setup-python"),
        ("frontend", "setup-node"),
    ):
        checkout, setup = workflow["jobs"][job_name]["steps"][:2]
        assert checkout["with"]["persist-credentials"] is False
        assert re.fullmatch(r"actions/checkout@[0-9a-f]{40}", checkout["uses"])
        assert re.fullmatch(
            rf"actions/{setup_action}@[0-9a-f]{{40}}", setup["uses"]
        )


def test_live_coverage_workflow_is_manual_read_only_and_pinned():
    workflow = _workflow(COVERAGE_WORKFLOW)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}

    job = workflow["jobs"]["coverage"]
    assert "environment" not in job
    serialized = COVERAGE_WORKFLOW.read_text().lower()
    assert "secrets." not in serialized
    assert "database_url" not in serialized
    assert "schedule:" not in serialized
    assert "pull_request" not in serialized
    assert "--fail-on-unhealthy" not in serialized

    checkout, setup = job["steps"][:2]
    assert checkout["with"]["persist-credentials"] is False
    assert re.fullmatch(r"actions/checkout@[0-9a-f]{40}", checkout["uses"])
    assert re.fullmatch(r"actions/setup-python@[0-9a-f]{40}", setup["uses"])
    upload = job["steps"][-1]
    assert re.fullmatch(r"actions/upload-artifact@[0-9a-f]{40}", upload["uses"])
