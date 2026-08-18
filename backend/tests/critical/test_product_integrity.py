"""Phase 1B.1 product identity, audit, consolidation, and database invariants."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.domain.product_identity import (
    canonicalize_product_url,
    prepare_product_observations,
    product_identity_key,
)
from tests.conftest import requires_db


pytestmark = pytest.mark.critical


def test_supported_scraper_identity_contract():
    # Shopify JSON, Storefront GraphQL, and sitemap all expose product:variant.
    assert product_identity_key("100:200") == "external-product:100"
    assert product_identity_key("100:999") == "external-product:100"
    # Salla exposes the product id directly.
    assert product_identity_key("1337747900") == "external-product:1337747900"
    # Generic Playwright has no external identity and relies on canonical URL.
    assert product_identity_key(None) is None


def test_canonical_url_is_conservative_and_tracking_insensitive():
    assert canonicalize_product_url(
        "HTTPS://www.Example.com:443/products/batwing/?utm_source=x&variant=2#reviews"
    ) == "https://example.com/products/batwing?variant=2"
    assert canonicalize_product_url(
        "https://example.com/product/batwing", "shopify_json"
    ) == "https://example.com/products/batwing"
    assert canonicalize_product_url(
        "https://example.com/products/batwing?variant=1", "shopify_json"
    ) != canonicalize_product_url(
        "https://example.com/products/batwing?variant=2", "shopify_json"
    )
    assert canonicalize_product_url(
        "https://example.com/en/products/batwing", "shopify_json"
    ) != canonicalize_product_url(
        "https://example.com/products/batwing", "shopify_json"
    )
    # Unknown query parameters are retained because generic stores can identify
    # products in the query string.
    assert canonicalize_product_url("https://example.com/item?id=2") != canonicalize_product_url(
        "https://example.com/item?id=3"
    )


def test_payload_deduplication_is_order_independent():
    payload = [
        {
            "title": "Sparse",
            "url": "https://example.com/products/x?utm_source=a",
            "external_id": "10:20",
            "price": None,
        },
        {
            "title": "Rich",
            "url": "https://example.com/products/renamed",
            "external_id": "10:21",
            "price": 5.0,
            "stock_status": "in_stock",
            "category": "MM2",
        },
    ]
    forward, forward_conflicts = prepare_product_observations(payload, "shopify_json")
    reverse, reverse_conflicts = prepare_product_observations(reversed(payload), "shopify_json")
    assert forward == reverse
    assert forward[0]["title"] == "Rich"
    assert forward_conflicts == reverse_conflicts


@requires_db
async def test_postgres_enforces_both_product_identity_invariants(migrated_database):
    from app.database import engine

    async with engine.connect() as connection:
        constraints = {
            row[0]
            for row in (
                await connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'products'::regclass"
                    )
                )
            ).all()
        }
        indexes = {
            row[0]
            for row in (
                await connection.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = 'products'")
                )
            ).all()
        }
    assert "uq_products_competitor_canonical_url" in constraints
    assert "uq_products_competitor_identity_key" in indexes


@requires_db
async def test_duplicate_audit_and_consolidation_preserve_all_history(migrated_database):
    """Representative Phase-1A duplicates are merged without losing dependencies."""
    from app.database import engine
    from scripts.consolidate_product_duplicates import consolidate
    from scripts.product_integrity_common import build_audit, load_product_rows, logical_duplicate_groups

    now = datetime.now(timezone.utc)
    older = now - timedelta(days=2)
    newer = now - timedelta(hours=1)

    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(
                text(
                    "TRUNCATE events, product_snapshots, products, scrape_runs, "
                    "competitors, app_settings RESTART IDENTITY CASCADE"
                )
            )
            # Reproduce the Phase 1A shape transactionally. Rollback restores the
            # head schema and constraints after this test.
            await connection.execute(
                text("DROP INDEX uq_products_competitor_identity_key")
            )
            await connection.execute(
                text(
                    "ALTER TABLE products DROP CONSTRAINT "
                    "uq_products_competitor_canonical_url"
                )
            )
            await connection.execute(text("ALTER TABLE products DROP COLUMN identity_key"))
            await connection.execute(text("ALTER TABLE products DROP COLUMN canonical_url"))
            competitor_id = (
                await connection.execute(
                    text(
                        "INSERT INTO competitors (name, base_url, scrape_type) "
                        "VALUES ('Fixture Store', 'https://fixture.example', 'shopify_json') "
                        "RETURNING id"
                    )
                )
            ).scalar_one()

            async def insert_product(title, url, external_id, price, checked_at):
                return (
                    await connection.execute(
                        text(
                            """
                            INSERT INTO products (
                                competitor_id, external_id, title, normalized_title, url,
                                current_price, currency, stock_status, first_seen_at,
                                last_seen_at, last_checked_at, active, consecutive_misses
                            ) VALUES (
                                :competitor_id, :external_id, :title, :normalized_title, :url,
                                :price, 'USD', 'in_stock', :checked_at, :checked_at,
                                :checked_at, true, 0
                            ) RETURNING id
                            """
                        ),
                        {
                            "competitor_id": competitor_id,
                            "external_id": external_id,
                            "title": title,
                            "normalized_title": title.lower(),
                            "url": url,
                            "price": price,
                            "checked_at": checked_at,
                        },
                    )
                ).scalar_one()

            first_id = await insert_product(
                "Old state",
                "https://fixture.example/products/item/?utm_source=old",
                "100:200",
                "5.00",
                older,
            )
            second_id = await insert_product(
                "New state",
                "https://fixture.example/product/item",
                "100:201",
                "7.00",
                newer,
            )
            for product_id, checked_at in ((first_id, older), (second_id, newer)):
                await connection.execute(
                    text(
                        "INSERT INTO product_snapshots "
                        "(product_id, title, price, currency, stock_status, checked_at) "
                        "VALUES (:id, 'History', 1, 'USD', 'in_stock', :checked_at)"
                    ),
                    {"id": product_id, "checked_at": checked_at},
                )
                await connection.execute(
                    text(
                        "INSERT INTO events (competitor_id, product_id, event_type) "
                        "VALUES (:competitor_id, :id, 'new_product')"
                    ),
                    {"competitor_id": competitor_id, "id": product_id},
                )

            rows, invalid = await load_product_rows(connection)
            audit = build_audit(rows, invalid)
            assert audit["summary"]["logical_duplicate_groups"] == 1
            assert audit["summary"]["affected_products"] == 2
            assert audit["logical_duplicate_groups"][0]["snapshot_count"] == 2
            assert audit["logical_duplicate_groups"][0]["event_count"] == 2

            result = await consolidate(connection, logical_duplicate_groups(rows))
            assert result == {
                "duplicate_rows_consolidated": 1,
                "snapshots_repointed": 1,
                "events_repointed": 1,
            }
            product = (
                await connection.execute(
                    text(
                        "SELECT id, title, current_price, first_seen_at, last_checked_at "
                        "FROM products"
                    )
                )
            ).mappings().one()
            assert product["id"] == first_id
            assert product["title"] == "New state"
            assert str(product["current_price"]) == "7.00"
            assert product["first_seen_at"] == older
            assert product["last_checked_at"] == newer
            assert (await connection.execute(text("SELECT count(*) FROM product_snapshots"))).scalar() == 2
            assert (await connection.execute(text("SELECT count(*) FROM events"))).scalar() == 2
            assert (
                await connection.execute(
                    text("SELECT count(DISTINCT product_id) FROM product_snapshots")
                )
            ).scalar() == 1
            assert (
                await connection.execute(text("SELECT count(DISTINCT product_id) FROM events"))
            ).scalar() == 1
        finally:
            await transaction.rollback()
