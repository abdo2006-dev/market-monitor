"""product category

Revision ID: 0002_product_category
Revises: 0001_initial
Create Date: 2026-05-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "0002_product_category"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("category", sa.String(100), nullable=True))
    op.create_index("ix_products_category", "products", ["category"])
    op.add_column("product_snapshots", sa.Column("category", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("product_snapshots", "category")
    op.drop_index("ix_products_category", table_name="products")
    op.drop_column("products", "category")
