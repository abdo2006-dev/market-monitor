from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from sqlalchemy.orm import selectinload
from typing import Optional, List
from datetime import datetime
from app.database import get_db
from app.models import Product, ProductSnapshot, Competitor
from app.schemas import ProductOut, SnapshotOut

router = APIRouter(prefix="/api/products", tags=["products"])


@router.get("", response_model=dict)
async def list_products(
    competitor_id: Optional[int] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    active: Optional[bool] = None,
    stock_status: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    sort: str = "last_checked_at",
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
):
    query = select(Product, Competitor.name.label("competitor_name")).join(
        Competitor, Product.competitor_id == Competitor.id
    )

    filters = []
    if competitor_id is not None:
        filters.append(Product.competitor_id == competitor_id)
    if active is not None:
        filters.append(Product.active == active)
    if category:
        filters.append(Product.category == category)
    if stock_status:
        filters.append(Product.stock_status == stock_status)
    if min_price is not None:
        filters.append(Product.current_price >= min_price)
    if max_price is not None:
        filters.append(Product.current_price <= max_price)
    if date_from:
        filters.append(Product.first_seen_at >= date_from)
    if date_to:
        filters.append(Product.first_seen_at <= date_to)
    if search:
        filters.append(Product.normalized_title.ilike(f"%{search.lower()}%"))

    if filters:
        query = query.where(and_(*filters))

    # Sort
    sort_col = {
        "last_checked_at": Product.last_checked_at.desc(),
        "price_asc": Product.current_price.asc(),
        "price_desc": Product.current_price.desc(),
        "first_seen": Product.first_seen_at.desc(),
        "title": Product.title.asc(),
    }.get(sort, Product.last_checked_at.desc())
    query = query.order_by(sort_col)

    # Count
    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar()

    # Paginate
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    rows = result.all()

    items = []
    for row in rows:
        product, competitor_name = row
        d = ProductOut.model_validate(product)
        d.competitor_name = competitor_name
        items.append(d)

    return {"items": [i.model_dump() for i in items], "total": total, "page": page, "page_size": page_size}


@router.get("/{product_id}", response_model=ProductOut)
async def get_product(product_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Product, Competitor.name.label("cname"))
        .join(Competitor)
        .where(Product.id == product_id)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")
    product, cname = row
    out = ProductOut.model_validate(product)
    out.competitor_name = cname
    return out


@router.get("/{product_id}/history", response_model=List[SnapshotOut])
async def get_product_history(product_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Product).where(Product.id == product_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Product not found")

    snaps = await db.execute(
        select(ProductSnapshot)
        .where(ProductSnapshot.product_id == product_id)
        .order_by(ProductSnapshot.checked_at.asc())
    )
    return snaps.scalars().all()
