"""Static release gates for the secret-bearing durable Sync workflow."""

from pathlib import Path

import yaml


WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "sync-v2.yml"


def _workflow() -> dict:
    document = yaml.safe_load(WORKFLOW.read_text())
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
    assert job["if"] == "github.event_name == 'schedule' || github.ref == 'refs/heads/main'"
    checkout = job["steps"][0]
    assert checkout["with"]["ref"] == "main"
    assert checkout["with"]["persist-credentials"] is False
    assert checkout["uses"].startswith("actions/checkout@")
    assert not any(key in job for key in ("permissions", "continue-on-error"))
