from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import engine
from app.models.models import Base

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI(
    title="EnglishForge API",
    description="Personal English practice app — BYOK, self-hosted, multi-user",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created/verified")

    if settings.APP_PIN:
        logger.info("APP_PIN protection is enabled")


@app.on_event("shutdown")
async def shutdown():
    await engine.dispose()


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME}


from app.routers.auth import router as auth_router
from app.routers.sessions import router as sessions_router
from app.routers.messages import router as messages_router
from app.routers.vocab import router as vocab_router
from app.routers.scenarios import router as scenarios_router
from app.routers.settings import router as settings_router
from app.routers.dashboard import router as dashboard_router
from app.routers.lessons import router as lessons_router
from app.routers.ws import router as ws_router

app.include_router(auth_router)
app.include_router(sessions_router)
app.include_router(messages_router)
app.include_router(vocab_router)
app.include_router(scenarios_router)
app.include_router(settings_router)
app.include_router(dashboard_router)
app.include_router(lessons_router)
app.include_router(ws_router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
