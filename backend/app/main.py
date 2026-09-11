from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.api.v1.endpoints.health import router as health_router
from app.core.config import get_settings
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.services.instant_leads import InstantLeadMonitor


settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    monitor = InstantLeadMonitor(SessionLocal)
    monitor.start()
    yield
    monitor.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.backend_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(api_router)
