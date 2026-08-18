from datetime import datetime, timedelta, timezone

from app.domain.search_trust import (
    CompetitorEvidence,
    RunEvidence,
    assess_search_trust,
    required_cairo_cycle_date,
)


def run(run_id: int, observed_at: datetime) -> RunEvidence:
    return RunEvidence(
        run_id=run_id,
        status="success",
        completeness="complete",
        observation_completed_at=observed_at,
        terminal_at=observed_at + timedelta(minutes=1),
    )


def test_cairo_cycle_keeps_yesterday_until_recovery_cutoff():
    before_recovery = datetime(2026, 8, 12, 8, 46, tzinfo=timezone(timedelta(hours=3)))
    after_recovery = datetime(2026, 8, 12, 8, 47, tzinfo=timezone(timedelta(hours=3)))

    assert required_cairo_cycle_date(before_recovery).isoformat() == "2026-08-11"
    assert required_cairo_cycle_date(after_recovery).isoformat() == "2026-08-12"


def test_complete_product_from_current_cycle_is_reliable_when_in_stock():
    now = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
    complete = run(7, now - timedelta(minutes=12))
    assessment = assess_search_trust(
        evidence=CompetitorEvidence(latest_complete=complete, latest_terminal=complete),
        product_observed_at=complete.observation_completed_at,
        product_run_id=7,
        has_price=True,
        stock_status="in_stock",
        now=now,
    )

    assert assessment.coverage_state == "current_complete"
    assert assessment.reliable is True
    assert assessment.product_observation_age_seconds == 720


def test_old_complete_cycle_is_stale_without_an_arbitrary_minute_threshold():
    now = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
    complete = run(7, now - timedelta(days=2))
    assessment = assess_search_trust(
        evidence=CompetitorEvidence(latest_complete=complete, latest_terminal=complete),
        product_observed_at=complete.observation_completed_at,
        product_run_id=7,
        has_price=True,
        stock_status="in_stock",
        now=now,
    )

    assert assessment.coverage_state == "stale"
    assert assessment.reliable is False


def test_current_complete_out_of_stock_price_remains_observed_but_not_reliable():
    now = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
    complete = run(8, now - timedelta(minutes=5))
    assessment = assess_search_trust(
        evidence=CompetitorEvidence(latest_complete=complete, latest_terminal=complete),
        product_observed_at=complete.observation_completed_at,
        product_run_id=8,
        has_price=True,
        stock_status="out_of_stock",
        now=now,
    )

    assert assessment.trustworthy_current_observation is True
    assert assessment.price_reliability == "degraded"
    assert assessment.reliable is False
    assert "not confirmed in stock" in assessment.warning
