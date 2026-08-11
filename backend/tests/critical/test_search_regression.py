"""
C1 — Market Search regression coverage.

Characterises the CURRENT behaviour of /api/search/suggestions,
/api/search/compare and the batch-compare endpoints against a real database.

Phase 1C preserves the regression-protected matching algorithm while adding
trust/freshness contracts and a guarded compare fast path. These tests keep any
future matching or market-semantics change visible and deliberate.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import ProductSnapshot, ScrapeRun
from tests.conftest import requires_db
from tests.critical.factories import hours_ago, make_competitor, make_product

pytestmark = [pytest.mark.critical, requires_db]


async def seed_market(session):
    """
    Three competitors selling overlapping Roblox items.

    Deliberately includes near-miss names that must NOT be merged.
    """
    alpha = await make_competitor(session, "Alpha Store", "https://alpha.example")
    beta = await make_competitor(session, "Beta Store", "https://beta.example")
    gamma = await make_competitor(session, "Gamma Store", "https://gamma.example")

    # Same item across all three, different prices.
    await make_product(session, alpha, "Batwing", price="12.00",
                       category="Murder Mystery 2", url="https://alpha.example/products/batwing")
    await make_product(session, beta, "Batwing", price="9.50",
                       category="Murder Mystery 2", url="https://beta.example/products/batwing")
    await make_product(session, gamma, "Batwing", price="15.00",
                       category="Murder Mystery 2", url="https://gamma.example/products/batwing")

    # A genuinely different item that shares no tokens.
    await make_product(session, alpha, "Chill Knife", price="4.00",
                       category="Murder Mystery 2", url="https://alpha.example/products/chill-knife")

    # A different collection entirely.
    await make_product(session, beta, "Huge Cat", price="200.00",
                       category="Pet Simulator 99", url="https://beta.example/products/huge-cat")

    await session.commit()
    return alpha, beta, gamma


async def make_search_run(
    session,
    competitor,
    *,
    status="success",
    completeness="complete",
    observed_at=None,
    terminal_at=None,
):
    observed_at = observed_at or datetime.now(timezone.utc) - timedelta(minutes=5)
    terminal_at = terminal_at or observed_at + timedelta(minutes=1)
    run = ScrapeRun(
        competitor_id=competitor.id,
        queued_at=observed_at - timedelta(minutes=2),
        started_at=observed_at - timedelta(minutes=1),
        finished_at=terminal_at if status not in {"queued", "running", "retry_wait"} else None,
        terminal_at=terminal_at if status not in {"queued", "running", "retry_wait"} else None,
        status=status,
        trigger="manual",
        acquisition_started_at=observed_at - timedelta(minutes=1),
        acquisition_completed_at=observed_at,
        observation_started_at=observed_at,
        observation_completed_at=(observed_at if status == "success" else None),
        completeness=completeness,
        products_found=1 if completeness != "suspicious_empty" else 0,
        attempt_count=1 if status != "queued" else 0,
    )
    session.add(run)
    await session.flush()
    return run


async def observe_product(product, run, observed_at=None):
    product.last_observed_at = observed_at or run.observation_completed_at
    product.last_observed_run_id = run.id


# ── Suggestions ───────────────────────────────────────────────────────────────

async def test_suggestions_exact_name_groups_across_competitors(db_session, api_client):
    await seed_market(db_session)

    resp = await api_client.get("/api/search/suggestions", params={"q": "Batwing"})
    assert resp.status_code == 200
    body = resp.json()

    batwing = [i for i in body["items"] if i["base_normalized_title"] == "batwing"]
    assert len(batwing) == 1, "one grouped suggestion, not one per competitor"

    group = batwing[0]
    assert group["competitors_count"] == 3
    assert sorted(group["competitors"]) == ["Alpha Store", "Beta Store", "Gamma Store"]
    assert float(group["best_price"]) == 9.50, "best price is the lowest across competitors"
    assert group["match_score"] > 0.9


async def test_suggestions_tolerates_imperfect_spelling(db_session, api_client):
    await seed_market(db_session)

    for typo in ("batwng", "Batwin", "batwing "):
        resp = await api_client.get("/api/search/suggestions", params={"q": typo})
        assert resp.status_code == 200
        titles = [i["base_normalized_title"] for i in resp.json()["items"]]
        assert "batwing" in titles, f"fuzzy search failed for {typo!r}"


async def test_suggestions_normalizes_case_and_punctuation(db_session, api_client):
    await seed_market(db_session)

    for variant in ("BATWING", "bat-wing", "  Batwing!  "):
        resp = await api_client.get("/api/search/suggestions", params={"q": variant})
        titles = [i["base_normalized_title"] for i in resp.json()["items"]]
        assert "batwing" in titles, f"normalization failed for {variant!r}"


async def test_suggestions_do_not_merge_unrelated_products(db_session, api_client):
    await seed_market(db_session)

    resp = await api_client.get("/api/search/suggestions", params={"q": "Batwing"})
    groups = {i["base_normalized_title"] for i in resp.json()["items"]}

    assert "chill knife" not in groups, "unrelated item must not join the Batwing group"
    assert "huge cat" not in groups


async def test_suggestions_exclude_inactive_products(db_session, api_client):
    alpha = await make_competitor(db_session, "Alpha Store", "https://alpha.example")
    await make_product(db_session, alpha, "Ghost Item", price="5.00",
                       url="https://alpha.example/products/ghost", active=False)
    await db_session.commit()

    resp = await api_client.get("/api/search/suggestions", params={"q": "Ghost Item"})
    assert resp.json()["items"] == [], "inactive products must not be searchable"


async def test_suggestions_no_results_returns_empty_not_error(db_session, api_client):
    await seed_market(db_session)

    resp = await api_client.get(
        "/api/search/suggestions", params={"q": "zzzzz nonexistent qqqq"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["query"] == "zzzzz nonexistent qqqq"


async def test_suggestions_empty_query_is_rejected(db_session, api_client):
    resp = await api_client.get("/api/search/suggestions", params={"q": ""})
    assert resp.status_code == 422, "min_length=1 is enforced by FastAPI"


async def test_suggestions_handles_missing_prices(db_session, api_client):
    alpha = await make_competitor(db_session, "Alpha Store", "https://alpha.example")
    beta = await make_competitor(db_session, "Beta Store", "https://beta.example")
    await make_product(db_session, alpha, "Priceless", price=None,
                       url="https://alpha.example/products/priceless")
    await make_product(db_session, beta, "Priceless", price="7.00",
                       url="https://beta.example/products/priceless")
    await db_session.commit()

    resp = await api_client.get("/api/search/suggestions", params={"q": "Priceless"})
    group = [i for i in resp.json()["items"] if i["base_normalized_title"] == "priceless"][0]

    assert group["competitors_count"] == 2
    assert float(group["best_price"]) == 7.00, "a null price must not win best-price"


async def test_suggestions_mutation_variants_are_separated(db_session, api_client):
    """
    Roblox 'mutations' (Rainbow, Gold, ...) are distinct market items. The
    identity engine splits them only inside collections it recognises as
    brainrot-like — see search_dashboard_settings.py:722.
    """
    alpha = await make_competitor(db_session, "Alpha Store", "https://alpha.example")
    await make_product(db_session, alpha, "Rainbow Noobini", price="30.00",
                       category="Steal a Brainrot",
                       url="https://alpha.example/products/rainbow-noobini")
    await make_product(db_session, alpha, "Noobini", price="10.00",
                       category="Steal a Brainrot",
                       url="https://alpha.example/products/noobini")
    await db_session.commit()

    resp = await api_client.get("/api/search/suggestions", params={"q": "Noobini"})
    items = resp.json()["items"]

    mutations = {i["mutation"] for i in items}
    assert "rainbow" in mutations, "mutation must be extracted as its own identity"
    assert "normal" in mutations
    assert len(items) >= 2, "plain and Rainbow are different market items"


async def test_suggestions_keep_same_title_in_different_collections_separate(
    db_session, api_client
):
    alpha = await make_competitor(db_session, "Alpha", "https://alpha.example")
    beta = await make_competitor(db_session, "Beta", "https://beta.example")
    await make_product(
        db_session, alpha, "Dragon", price="10.00", category="Adopt Me",
        url="https://alpha.example/products/dragon",
    )
    await make_product(
        db_session, beta, "Dragon", price="20.00", category="Blox Fruits",
        url="https://beta.example/products/dragon",
    )
    await db_session.commit()

    items = (await api_client.get(
        "/api/search/suggestions", params={"q": "Dragon"}
    )).json()["items"]
    dragon_groups = [item for item in items if item["base_normalized_title"] == "dragon"]
    assert {item["category"] for item in dragon_groups} == {"Adopt Me", "Blox Fruits"}


async def test_suggestions_do_not_compare_numeric_prices_across_currencies(
    db_session, api_client
):
    alpha = await make_competitor(db_session, "USD Store", "https://usd.example")
    beta = await make_competitor(db_session, "EUR Store", "https://eur.example")
    await make_product(
        db_session, alpha, "Batwing", price="10.00", currency="USD",
        category="Murder Mystery 2", url="https://usd.example/products/batwing",
    )
    await make_product(
        db_session, beta, "Batwing", price="2.00", currency="EUR",
        category="Murder Mystery 2", url="https://eur.example/products/batwing",
    )
    await db_session.commit()

    item = (await api_client.get(
        "/api/search/suggestions", params={"q": "Batwing"}
    )).json()["items"][0]
    assert item["best_price"] is None
    assert item["currency"] == "MULTI"
    assert item["prices_by_currency"] == [
        {"currency": "EUR", "lowest_observed_price": 2.0},
        {"currency": "USD", "lowest_observed_price": 10.0},
    ]


# ── Compare ───────────────────────────────────────────────────────────────────

async def test_compare_lists_every_active_competitor_and_sorts_by_price(
    db_session, api_client
):
    alpha, beta, gamma = await seed_market(db_session)

    resp = await api_client.get("/api/search/compare", params={"q": "Batwing"})
    assert resp.status_code == 200
    body = resp.json()

    assert body["target"] is not None
    assert body["total_matches"] == 3

    rows = body["items"]
    assert len(rows) == 3, "one row per active competitor"

    prices = [r["product"]["current_price"] for r in rows if r["product"]]
    assert prices == sorted(prices), "cheapest first"
    assert float(prices[0]) == 9.50
    assert rows[0]["competitor_name"] == "Beta Store"


async def test_compare_reports_unmatched_competitors_as_null_products(
    db_session, api_client
):
    alpha = await make_competitor(db_session, "Alpha Store", "https://alpha.example")
    await make_competitor(db_session, "Empty Store", "https://empty.example")
    await make_product(db_session, alpha, "Batwing", price="12.00",
                       url="https://alpha.example/products/batwing")
    await db_session.commit()

    body = (await api_client.get("/api/search/compare", params={"q": "Batwing"})).json()

    assert len(body["items"]) == 2
    assert body["total_matches"] == 1
    unmatched = [r for r in body["items"] if r["product"] is None]
    assert len(unmatched) == 1
    assert unmatched[0]["competitor_name"] == "Empty Store"
    assert unmatched[0]["match_score"] == 0


async def test_compare_excludes_inactive_competitors(db_session, api_client):
    alpha = await make_competitor(db_session, "Alpha Store", "https://alpha.example")
    await make_competitor(db_session, "Retired", "https://retired.example", active=False)
    await make_product(db_session, alpha, "Batwing", price="12.00",
                       url="https://alpha.example/products/batwing")
    await db_session.commit()

    body = (await api_client.get("/api/search/compare", params={"q": "Batwing"})).json()

    names = {r["competitor_name"] for r in body["items"]}
    assert "Retired" not in names


async def test_compare_does_not_match_across_collections(db_session, api_client):
    """
    _collections_compatible (search_dashboard_settings.py:802) blocks a match
    when both sides have a known but different collection.
    """
    alpha = await make_competitor(db_session, "Alpha Store", "https://alpha.example")
    beta = await make_competitor(db_session, "Beta Store", "https://beta.example")
    await make_product(db_session, alpha, "Dragon", price="10.00",
                       category="Adopt Me", url="https://alpha.example/products/dragon")
    await make_product(db_session, beta, "Dragon", price="20.00",
                       category="Blox Fruits", url="https://beta.example/products/dragon")
    await db_session.commit()

    body = (await api_client.get("/api/search/compare", params={"q": "Dragon"})).json()

    matched = [r for r in body["items"] if r["product"]]
    assert len(matched) == 1, (
        "same word in two different games must not be treated as one market item"
    )


async def test_compare_does_not_promote_a_named_bundle_as_the_base_product(
    db_session, api_client
):
    alpha = await make_competitor(db_session, "Alpha", "https://alpha.example")
    beta = await make_competitor(db_session, "Beta", "https://beta.example")
    await make_product(
        db_session, alpha, "Batwing", price="10.00", category="Murder Mystery 2",
        url="https://alpha.example/products/batwing",
    )
    await make_product(
        db_session, beta, "Batwing Bundle", price="2.00", category="Murder Mystery 2",
        url="https://beta.example/products/batwing-bundle",
    )
    await db_session.commit()

    body = (await api_client.get("/api/search/compare", params={"q": "Batwing"})).json()
    beta_row = next(row for row in body["items"] if row["competitor_name"] == "Beta")
    assert beta_row["product"] is None, "a bundle is a different market offer"


async def test_compare_fallback_preserves_fuzzy_match_outside_fast_path(
    db_session, api_client
):
    alpha = await make_competitor(db_session, "Alpha", "https://alpha.example")
    beta = await make_competitor(db_session, "Beta", "https://beta.example")
    target = await make_product(
        db_session, alpha, "abcdefgh", price="10.00",
        url="https://alpha.example/products/abcdefgh",
    )
    await make_product(
        db_session, beta, "xbcdefgh", price="12.00",
        url="https://beta.example/products/xbcdefgh",
    )
    await db_session.commit()

    body = (await api_client.get(
        "/api/search/compare", params={"product_id": target.id}
    )).json()

    assert body["total_matches"] == 2
    beta_row = next(row for row in body["items"] if row["competitor_name"] == "Beta")
    assert beta_row["product"]["title"] == "xbcdefgh"
    assert beta_row["match_score"] == 0.875


async def test_compare_by_product_id_is_exact(db_session, api_client):
    alpha, beta, _ = await seed_market(db_session)

    suggestions = (
        await api_client.get("/api/search/suggestions", params={"q": "Batwing"})
    ).json()["items"]
    representative = suggestions[0]["representative_product_id"]

    body = (
        await api_client.get(
            "/api/search/compare", params={"product_id": representative}
        )
    ).json()

    assert body["target"]["id"] == representative
    assert body["total_matches"] == 3


async def test_compare_no_match_returns_empty_envelope(db_session, api_client):
    await seed_market(db_session)

    body = (
        await api_client.get("/api/search/compare", params={"q": "zzzz nothing zzzz"})
    ).json()

    assert body["target"] is None
    assert body["items"] == []
    assert body["total_matches"] == 0


async def test_compare_with_no_arguments_is_rejected(db_session, api_client):
    """The typed compare contract requires one unambiguous target selector."""
    resp = await api_client.get("/api/search/compare")
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Provide q or product_id."


async def test_compare_separates_lowest_reliable_from_lower_stale_observation(
    db_session, api_client
):
    alpha = await make_competitor(db_session, "Alpha Store", "https://alpha.example")
    beta = await make_competitor(db_session, "Beta Store", "https://beta.example")

    stale_time = datetime.now(timezone.utc) - timedelta(days=3)
    stale_run = await make_search_run(
        db_session, alpha, observed_at=stale_time, terminal_at=stale_time + timedelta(minutes=1)
    )
    current_run = await make_search_run(db_session, beta)
    stale_product = await make_product(
        db_session, alpha, "Batwing", price="4.00",
        url="https://alpha.example/products/batwing", last_checked_at=stale_time,
    )
    current_product = await make_product(
        db_session, beta, "Batwing", price="9.00",
        url="https://beta.example/products/batwing",
    )
    await observe_product(stale_product, stale_run)
    await observe_product(current_product, current_run)
    await db_session.commit()

    body = (await api_client.get("/api/search/compare", params={"q": "Batwing"})).json()
    summary = body["market_summary"]
    usd = summary["currencies"][0]

    assert usd["lowest_observed_price"] == 4.0
    assert usd["lowest_observed_competitor_name"] == "Alpha Store"
    assert usd["lowest_reliable_price"] == 9.0
    assert usd["lowest_reliable_competitor_name"] == "Beta Store"
    assert summary["trustworthy_current"] == 1
    assert summary["degraded_or_unknown"] == 1

    rows = {row["competitor_name"]: row for row in body["items"]}
    assert rows["Alpha Store"]["trust"]["coverage_state"] == "stale"
    assert rows["Alpha Store"]["trust"]["reliable"] is False
    assert rows["Alpha Store"]["trust"]["product_observation_age_seconds"] > 172800
    assert rows["Beta Store"]["trust"]["coverage_state"] == "current_complete"
    assert rows["Beta Store"]["trust"]["reliable"] is True


async def test_compare_market_range_uses_only_current_complete_in_stock_prices(
    db_session, api_client
):
    prices = ("9.00", "12.00", "15.00")
    for index, price in enumerate(prices):
        competitor = await make_competitor(
            db_session, f"Store {index}", f"https://store-{index}.example"
        )
        run = await make_search_run(db_session, competitor)
        product = await make_product(
            db_session, competitor, "Batwing", price=price,
            url=f"https://store-{index}.example/products/batwing",
        )
        await observe_product(product, run)
    out_of_stock = await make_competitor(db_session, "No Stock", "https://nostock.example")
    out_run = await make_search_run(db_session, out_of_stock)
    out_product = await make_product(
        db_session, out_of_stock, "Batwing", price="2.00", stock_status="out_of_stock",
        url="https://nostock.example/products/batwing",
    )
    await observe_product(out_product, out_run)
    await db_session.commit()

    body = (await api_client.get("/api/search/compare", params={"q": "Batwing"})).json()
    usd = body["market_summary"]["currencies"][0]
    assert usd["lowest_observed_price"] == 2.0
    assert usd["lowest_reliable_price"] == 9.0
    assert usd["highest_reliable_price"] == 15.0
    assert usd["median_reliable_price"] == 12.0
    assert usd["reliable_price_count"] == 3
    no_stock = next(row for row in body["items"] if row["competitor_name"] == "No Stock")
    assert no_stock["trust"]["trustworthy_current_observation"] is True
    assert no_stock["trust"]["reliable"] is False
    assert "not confirmed in stock" in no_stock["trust"]["warning"]


@pytest.mark.parametrize(
    ("completeness", "expected_state"),
    [("partial", "partial"), ("suspicious_empty", "suspicious_empty")],
)
async def test_compare_marks_latest_incomplete_catalog_without_hiding_price(
    db_session, api_client, completeness, expected_state
):
    competitor = await make_competitor(db_session, "Degraded", "https://degraded.example")
    complete = await make_search_run(db_session, competitor)
    product = await make_product(
        db_session, competitor, "Batwing", price="5.00",
        url="https://degraded.example/products/batwing",
    )
    await observe_product(product, complete)
    later = complete.terminal_at + timedelta(minutes=5)
    incomplete = await make_search_run(
        db_session,
        competitor,
        completeness=completeness,
        observed_at=later,
        terminal_at=later + timedelta(minutes=1),
    )
    if completeness == "partial":
        await observe_product(product, incomplete)
    await db_session.commit()

    body = (await api_client.get("/api/search/compare", params={"q": "Batwing"})).json()
    row = body["items"][0]
    assert row["product"]["current_price"] == 5.0
    assert row["trust"]["coverage_state"] == expected_state
    assert row["trust"]["reliable"] is False
    assert body["market_summary"]["no_reliable_prices"] is True
    assert body["market_summary"]["currencies"][0]["lowest_observed_price"] == 5.0


async def test_compare_marks_failed_attempt_and_preserves_prior_observation(
    db_session, api_client
):
    competitor = await make_competitor(db_session, "Failed Store", "https://failed.example")
    complete = await make_search_run(db_session, competitor)
    product = await make_product(
        db_session, competitor, "Batwing", price="8.00",
        url="https://failed.example/products/batwing",
    )
    await observe_product(product, complete)
    failed_at = complete.terminal_at + timedelta(minutes=10)
    await make_search_run(
        db_session,
        competitor,
        status="failed",
        completeness="failed",
        observed_at=failed_at,
        terminal_at=failed_at,
    )
    await db_session.commit()

    row = (await api_client.get(
        "/api/search/compare", params={"q": "Batwing"}
    )).json()["items"][0]
    assert row["product"]["current_price"] == 8.0
    assert row["trust"]["coverage_state"] == "failed"
    assert row["trust"]["last_failed_at"] is not None
    assert row["trust"]["producing_run"]["run_id"] == complete.id


async def test_compare_legacy_lineage_is_unknown_and_never_promoted(db_session, api_client):
    competitor = await make_competitor(db_session, "Legacy", "https://legacy.example")
    await make_product(
        db_session, competitor, "Batwing", price="3.00",
        url="https://legacy.example/products/batwing",
    )
    await db_session.commit()

    body = (await api_client.get("/api/search/compare", params={"q": "Batwing"})).json()
    row = body["items"][0]
    assert row["trust"]["coverage_state"] == "unknown"
    assert row["trust"]["price_reliability"] == "unknown"
    assert row["trust"]["producing_run"] is None
    assert body["market_summary"]["currencies"][0]["lowest_reliable_price"] is None


async def test_compare_exposes_active_sync_without_claiming_refresh_completed(
    db_session, api_client
):
    competitor = await make_competitor(db_session, "Syncing", "https://syncing.example")
    complete = await make_search_run(db_session, competitor)
    product = await make_product(
        db_session, competitor, "Batwing", price="6.00",
        url="https://syncing.example/products/batwing",
    )
    await observe_product(product, complete)
    active = await make_search_run(
        db_session,
        competitor,
        status="running",
        completeness="unknown",
        observed_at=datetime.now(timezone.utc),
    )
    await db_session.commit()

    body = (await api_client.get("/api/search/compare", params={"q": "Batwing"})).json()
    row = body["items"][0]
    assert row["trust"]["active_sync"]["run_id"] == active.id
    assert row["trust"]["active_sync"]["status"] == "running"
    assert row["trust"]["coverage_state"] == "current_complete"
    assert body["market_summary"]["syncing_competitors"] == 1


async def test_compare_never_combines_currencies(db_session, api_client):
    for name, currency, price in (("USD Store", "USD", "10.00"), ("EUR Store", "EUR", "2.00")):
        competitor = await make_competitor(
            db_session, name, f"https://{currency.lower()}.example"
        )
        run = await make_search_run(db_session, competitor)
        product = await make_product(
            db_session, competitor, "Batwing", price=price, currency=currency,
            url=f"https://{currency.lower()}.example/products/batwing",
        )
        await observe_product(product, run)
    await db_session.commit()

    body = (await api_client.get("/api/search/compare", params={"q": "Batwing"})).json()
    summaries = {item["currency"]: item for item in body["market_summary"]["currencies"]}
    assert summaries["USD"]["lowest_reliable_price"] == 10.0
    assert summaries["EUR"]["lowest_reliable_price"] == 2.0
    assert len(summaries) == 2


async def test_compare_price_change_comes_from_direct_snapshots(db_session, api_client):
    competitor = await make_competitor(db_session, "History", "https://history.example")
    run = await make_search_run(db_session, competitor)
    product = await make_product(
        db_session, competitor, "Batwing", price="8.00",
        url="https://history.example/products/batwing",
    )
    await observe_product(product, run)
    previous_at = run.observation_completed_at - timedelta(hours=1)
    db_session.add_all([
        ProductSnapshot(
            product_id=product.id, title=product.title, price="10.00", currency="USD",
            stock_status="in_stock", checked_at=previous_at, observed_at=previous_at,
        ),
        ProductSnapshot(
            product_id=product.id, title=product.title, price="8.00", currency="USD",
            stock_status="in_stock", checked_at=run.terminal_at,
            observed_at=run.observation_completed_at, scrape_run_id=run.id,
        ),
    ])
    await db_session.commit()

    row = (await api_client.get(
        "/api/search/compare", params={"q": "Batwing"}
    )).json()["items"][0]
    assert row["price_change"] == {
        "previous_price": 10.0,
        "current_price": 8.0,
        "currency": "USD",
        "direction": "decrease",
        "amount": 2.0,
        "percentage": 20.0,
        "changed_at": run.observation_completed_at.isoformat().replace("+00:00", "Z"),
        "scrape_run_id": run.id,
    }


# ── Batch compare ─────────────────────────────────────────────────────────────

async def test_batch_compare_summary_returns_row_per_query(db_session, api_client):
    await seed_market(db_session)

    resp = await api_client.get(
        "/api/search/batch-compare-summary",
        params=[("queries", "Batwing"), ("queries", "Chill Knife")],
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["total"] == 2
    by_query = {i["query"]: i for i in body["items"]}

    assert float(by_query["Batwing"]["lowest_price"]) == 9.50
    assert by_query["Batwing"]["lowest_seller"] == "Beta Store"
    assert by_query["Batwing"]["total_matches"] == 3

    assert by_query["Chill Knife"]["total_matches"] == 1


async def test_batch_compare_summary_reports_unmatched_query(db_session, api_client):
    await seed_market(db_session)

    body = (
        await api_client.get(
            "/api/search/batch-compare-summary", params={"queries": "zzzz nothing"}
        )
    ).json()

    item = body["items"][0]
    assert item["matched_item"] is None
    assert item["lowest_price"] is None
    assert item["total_matches"] == 0
    assert item["competitor_prices"] == []


async def test_batch_compare_summary_expands_comma_separated_queries(
    db_session, api_client
):
    await seed_market(db_session)

    body = (
        await api_client.get(
            "/api/search/batch-compare-summary",
            params={"queries": "Batwing, Chill Knife"},
        )
    ).json()

    assert body["total"] == 2
    assert {i["query"] for i in body["items"]} == {"Batwing", "Chill Knife"}


@pytest.mark.parametrize("fmt", ["markdown", "csv"])
async def test_batch_compare_summary_alternate_formats(db_session, api_client, fmt):
    await seed_market(db_session)

    resp = await api_client.get(
        "/api/search/batch-compare-summary",
        params={"queries": "Batwing", "format": fmt},
    )
    assert resp.status_code == 200
    text = resp.text
    assert "Batwing" in text
    assert "Beta Store" in text
    assert "9.50" in text


async def test_batch_compare_summary_rejects_too_many_queries(db_session, api_client):
    await seed_market(db_session)

    many = ",".join(f"item{i}" for i in range(150))
    resp = await api_client.get(
        "/api/search/batch-compare-summary", params={"queries": many}
    )
    assert resp.status_code == 422
    assert "up to 100" in resp.json()["detail"]
