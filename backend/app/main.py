from contextlib import asynccontextmanager
import logging
import os
import socket
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.api.v1.endpoints.health import router as health_router
from app.core.config import get_settings
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.services.instant_leads import InstantLeadMonitor
from app.services.campaign_scheduler import campaign_scheduler


settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    monitor = InstantLeadMonitor(SessionLocal)
    logger.info("[LIFESPAN_SCHEDULER_STARTING] pid=%s thread=%s environment=%s hostname=%s", os.getpid(), threading.get_ident(), settings.environment, socket.gethostname())
    try:
        campaign_scheduler.start()
    except Exception:
        logger.exception("[LIFESPAN_SCHEDULER_FAILED] pid=%s thread=%s environment=%s", os.getpid(), threading.get_ident(), settings.environment)
        raise
    logger.info("[LIFESPAN_SCHEDULER_STARTED] pid=%s thread=%s environment=%s scheduler_alive=%s", os.getpid(), threading.get_ident(), settings.environment, bool(campaign_scheduler._thread and campaign_scheduler._thread.is_alive()))
    monitor.start()
    yield
    monitor.stop()
    campaign_scheduler.stop()


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
