"""add canonical product identity invariants

Revision ID: 0004_product_identity_integrity
Revises: 0003_reconcile_index_names

This migration deliberately refuses to choose winners when duplicate logical
products exist. Run the read-only audit, take a backup, and use the explicit
consolidation script before retrying the migration.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from alembic import op
import sqlalchemy as sa

revision = "0004_product_identity_integrity"
down_revision = "0003_reconcile_index_names"
branch_labels = None
depends_on = None


# Migration-local copies keep historical upgrades deterministic if the runtime
# identity module evolves later.
def _canonicalize_product_url(url: str, scrape_type: str | None = None) -> str:
    parsed = urlsplit((url or "").strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("product URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise RuntimeError("product URL must not contain credentials")
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower().removeprefix("www.")
    port = parsed.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    if scrape_type == "shopify_json" and path.startswith("/product/"):
        path = "/products/" + path[len("/product/") :]
    query = sorted(
        (name, value)
        for name, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not name.lower().startswith("utm_")
        and name.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}
    )
    return urlunsplit((scheme, host, path, urlencode(query, doseq=True), ""))


def _product_identity_key(external_id: str | None) -> str | None:
    value = str(external_id or "").strip()
    if not value or value.lower() in {"none", "null"} or value.startswith("None:"):
        return None
    pair = re.fullmatch(r"(\d+):(\d+)", value)
    if pair:
        value = pair.group(1)
    return f"external-product:{value}"


def upgrade() -> None:
    op.add_column("products", sa.Column("canonical_url", sa.String(1000), nullable=True))
    op.add_column("products", sa.Column("identity_key", sa.String(320), nullable=True))

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT p.id, p.url, p.external_id, c.scrape_type
            FROM products AS p
            JOIN competitors AS c ON c.id = p.competitor_id
            ORDER BY p.id
            """
        )
    ).mappings()
    for row in rows:
        canonical_url = _canonicalize_product_url(row["url"], row["scrape_type"])
        identity_key = _product_identity_key(row["external_id"])
        connection.execute(
            sa.text(
                "UPDATE products SET canonical_url = :canonical_url, identity_key = :identity_key "
                "WHERE id = :product_id"
            ),
            {
                "canonical_url": canonical_url,
                "identity_key": identity_key,
                "product_id": row["id"],
            },
        )

    duplicate_urls = connection.execute(
        sa.text(
            """
            SELECT competitor_id, canonical_url, array_agg(id ORDER BY id) AS product_ids
            FROM products
            GROUP BY competitor_id, canonical_url
            HAVING count(*) > 1
            LIMIT 1
            """
        )
    ).mappings().first()
    duplicate_keys = connection.execute(
        sa.text(
            """
            SELECT competitor_id, identity_key, array_agg(id ORDER BY id) AS product_ids
            FROM products
            WHERE identity_key IS NOT NULL
            GROUP BY competitor_id, identity_key
            HAVING count(*) > 1
            LIMIT 1
            """
        )
    ).mappings().first()
    if duplicate_urls or duplicate_keys:
        sample = duplicate_urls or duplicate_keys
        raise RuntimeError(
            "Logical product duplicates block migration 0004. "
            f"Sample competitor_id={sample['competitor_id']}, "
            f"product_ids={list(sample['product_ids'])}. "
            "Run scripts/audit_product_duplicates.py (read-only), take a backup, "
            "then run scripts/consolidate_product_duplicates.py explicitly."
        )

    op.alter_column("products", "canonical_url", nullable=False)
    op.create_unique_constraint(
        "uq_products_competitor_canonical_url",
        "products",
        ["competitor_id", "canonical_url"],
    )
    op.create_index(
        "uq_products_competitor_identity_key",
        "products",
        ["competitor_id", "identity_key"],
        unique=True,
        postgresql_where=sa.text("identity_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_products_competitor_identity_key", table_name="products")
    op.drop_constraint(
        "uq_products_competitor_canonical_url", "products", type_="unique"
    )
    op.drop_column("products", "identity_key")
    op.drop_column("products", "canonical_url")
