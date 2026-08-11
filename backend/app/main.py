import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import SchemaStateError, verify_schema_state
from app.api.competitors import router as competitors_router
from app.api.products import router as products_router
from app.api.events import router as events_router
from app.api.exports import router as exports_router
from app.api.search_dashboard_settings import (
    search_router, dashboard_router, settings_router
)
from app.api.cron import router as cron_router
from app.api.sync import router as sync_router

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Market Monitor API",
    description="Competitor price monitoring system",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(competitors_router)
app.include_router(products_router)
app.include_router(events_router)
app.include_router(exports_router)
app.include_router(search_router)
app.include_router(dashboard_router)
app.include_router(settings_router)
app.include_router(cron_router)
app.include_router(sync_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.on_event("startup")
async def startup():
    # Alembic is the single schema authority (docs/adr/0002). Startup verifies
    # the schema state and reports loudly; it never creates or alters schema.
    # A database that is unreachable at boot must not prevent the process from
    # starting - the check is a diagnostic, not a liveness gate.
    try:
        await verify_schema_state()
    except SchemaStateError:
        raise
    except Exception as exc:
        logger.warning("Could not verify database schema state at startup: %s", exc)
