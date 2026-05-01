from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from app.config import settings


def _normalize_database_url(url: str) -> tuple[str, dict]:
    connect_args = {}
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]

    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)
    if sslmode in {"require", "prefer", "verify-ca", "verify-full"}:
        connect_args["ssl"] = True
    url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    return url, connect_args


database_url, database_connect_args = _normalize_database_url(settings.DATABASE_URL)
engine = create_async_engine(
    database_url,
    echo=False,
    pool_pre_ping=True,
    poolclass=NullPool,
    connect_args=database_connect_args,
)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("ALTER TABLE products ADD COLUMN IF NOT EXISTS category VARCHAR(100)"))
        await conn.execute(text("ALTER TABLE product_snapshots ADD COLUMN IF NOT EXISTS category VARCHAR(100)"))
