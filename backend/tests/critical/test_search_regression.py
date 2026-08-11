"""
C1 — Market Search regression coverage.

Characterises the CURRENT behaviour of /api/search/suggestions,
/api/search/compare and the batch-compare endpoints against a real database.

Phase 1A deliberately does NOT rewrite the matching algorithm. These tests pin
down what it does today — including where it is wrong — so that any future change
is a visible, deliberate one. Cases that expose incorrect behaviour are marked
BUG and assert what actually happens.
"""
from __future__ import annotations

import pytest

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


async def test_compare_with_no_arguments_returns_empty_envelope(db_session, api_client):
    """BUG (documented): /search/compare has no required parameter, so calling it
    with neither q nor product_id returns 200 and an empty envelope rather than
    422. Characterising current behaviour."""
    resp = await api_client.get("/api/search/compare")
    assert resp.status_code == 200
    assert resp.json()["target"] is None


async def test_compare_ignores_freshness(db_session, api_client):
    """
    BUG (documented): compare surfaces prices with no freshness filter or
    signal. A price last checked weeks ago ranks identically to one checked
    minutes ago, and the response carries no staleness indicator beyond the raw
    last_checked_at on each product.

    This is the core motivation for the freshness model in
    docs/DAILY_CRITICAL_WORKFLOWS.md - the owner makes pricing decisions from
    this screen.
    """
    alpha = await make_competitor(db_session, "Alpha Store", "https://alpha.example")
    beta = await make_competitor(db_session, "Beta Store", "https://beta.example")

    await make_product(db_session, alpha, "Batwing", price="4.00",
                       url="https://alpha.example/products/batwing",
                       last_checked_at=hours_ago(24 * 30))   # a month old
    await make_product(db_session, beta, "Batwing", price="9.00",
                       url="https://beta.example/products/batwing",
                       last_checked_at=hours_ago(0.1))       # minutes old
    await db_session.commit()

    body = (await api_client.get("/api/search/compare", params={"q": "Batwing"})).json()
    rows = [r for r in body["items"] if r["product"]]

    # The month-old price wins purely because it is numerically lower.
    assert float(rows[0]["product"]["current_price"]) == 4.00
    assert rows[0]["competitor_name"] == "Alpha Store"

    # Nothing in the payload flags it as stale.
    assert "freshness" not in rows[0]
    assert "is_stale" not in rows[0]["product"]
    assert rows[0]["product"]["last_checked_at"] is not None, (
        "the raw timestamp is present - a future freshness model can derive from it"
    )


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
