import logging

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from app.config import settings

logger = logging.getLogger(__name__)


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


class SchemaStateError(RuntimeError):
    """The database schema is not in a state this application can safely use."""


def _migration_head() -> str | None:
    """Head revision according to the migration scripts on disk."""
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend_dir = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()


async def verify_schema_state() -> dict:
    """
    Check that the database is managed by Alembic and at the expected revision.

    This function NEVER creates or alters schema. Alembic is the single schema
    authority (docs/adr/0002-postgres-source-of-truth.md); the application only
    verifies. Returns a report dict; raises SchemaStateError only in strict mode.
    """
    import app.models  # noqa: F401  (ensure metadata is populated)

    head = _migration_head()
    stamped = None
    tables_present = 0

    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'alembic_version')"
            )
        )
        has_version_table = bool(result.scalar())

        if has_version_table:
            stamped = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()

        tables_present = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = current_schema() AND table_name <> 'alembic_version'"
                )
            )
        ).scalar() or 0

    # An alembic_version table with no row is exactly as unmanaged as a missing
    # one, so both are treated as "unstamped".
    is_stamped = has_version_table and stamped is not None

    if not is_stamped and tables_present == 0:
        problem = (
            "Database is empty and no migrations have been applied. "
            "Run: alembic upgrade head"
        )
    elif not is_stamped:
        problem = (
            f"Database has {tables_present} tables but no alembic_version stamp "
            f"(version table {'present but empty' if has_version_table else 'absent'}). "
            "It was probably created by a pre-Phase-1A build that ran create_all at "
            "startup. Run scripts/check_schema_state.py, take a backup, then stamp it. "
            "See docs/RUNBOOK.md section 2.2."
        )
    elif head and stamped != head:
        problem = (
            f"Database is at Alembic revision {stamped!r} but the code expects {head!r}. "
            "Run: alembic upgrade head"
        )
    else:
        problem = None

    report = {
        "ok": problem is None,
        "problem": problem,
        "stamped_revision": stamped,
        "migration_head": head,
        "tables_present": tables_present,
    }

    if problem is None:
        logger.info("Schema check OK (revision %s)", stamped)
        return report

    mode = (settings.DB_SCHEMA_CHECK or "warn").lower()
    message = f"DATABASE SCHEMA CHECK FAILED: {problem}"

    if mode == "off":
        logger.warning("%s (DB_SCHEMA_CHECK=off, continuing)", message)
    elif mode == "strict":
        logger.error(message)
        raise SchemaStateError(message)
    else:
        # Default. Loud, but does not take a running deployment down on boot.
        # Flip to strict once production has been verified - see docs/RUNBOOK.md.
        logger.error("%s (DB_SCHEMA_CHECK=warn, continuing)", message)

    return report
