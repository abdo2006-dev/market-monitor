from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import init_db
from app.api.competitors import router as competitors_router
from app.api.products import router as products_router
from app.api.events import router as events_router
from app.api.search_dashboard_settings import (
    search_router, dashboard_router, settings_router
)
from app.api.cron import router as cron_router

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
app.include_router(search_router)
app.include_router(dashboard_router)
app.include_router(settings_router)
app.include_router(cron_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.on_event("startup")
async def startup():
    await init_db()
