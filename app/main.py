"""
app/main.py — FastAPI application factory.

This is the only place that creates the FastAPI app, registers middleware,
mounts static files, and includes all API routers.
"""
import logging
import importlib
import os
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.core.logging import configure_logging
from app.core.config import (
    CORS_ALLOWED_ORIGINS,
    IMAGE_PIPELINE_MAX_CONCURRENCY,
    IMAGE_PIPELINE_QUEUE_SECONDS,
)
from app.api import auth, detections, drone, expert, health, monitoring, notifications, records, recommendations, reports, farm_boundaries
from app.api import tree_identity
from app.api import media

configure_logging()
logger = logging.getLogger(__name__)
_image_pipeline_slots = asyncio.Semaphore(IMAGE_PIPELINE_MAX_CONCURRENCY)


def _is_memory_intensive_image_pipeline(request: Request) -> bool:
    """Identify routes that retain a source image while decoding/inferencing it."""
    if request.method != "POST":
        return False
    return (request.url.path == "/detect/image"
            or request.url.path.startswith("/monitoring/cases/")
            and request.url.path.endswith("/follow-ups"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    logger.info("=" * 70)
    logger.info("🚀 Coconut Disease Detector — Starting")
    logger.info("=" * 70)

    # Initialise Firebase Admin SDK (side-effect import).
    import app.infrastructure.firebase.admin  # noqa: F401

    # Import once during startup so the first upload does not initialize YOLO
    # on the event loop. Every inference path reuses model.detector.
    await run_in_threadpool(importlib.import_module, "model")

    # Initialise Drone GPS.
    try:
        from drone_gps import init_drone_gps  # noqa: PLC0415
        drone_gps = init_drone_gps(drone_ip="192.168.1.1", port=8889, use_simulation=False)
        connected = await drone_gps.connect()
        if connected:
            logger.info("✅ Drone GPS initialised (EXIF / browser geolocation mode)")
        else:
            logger.warning("⚠️  Drone GPS connection failed (will use browser location)")
    except Exception as exc:
        logger.warning("⚠️  Drone GPS init error: %s", exc)

    # Ensure the expert account exists in Firebase Auth.
    try:
        from app.core.config import EXPERT_ACCOUNT_EMAIL, EXPERT_ACCOUNT_PASSWORD  # noqa: PLC0415
        from app.infrastructure.firebase.auth import ensure_user_account  # noqa: PLC0415
        expert_user, created = await run_in_threadpool(
            ensure_user_account, EXPERT_ACCOUNT_EMAIL, EXPERT_ACCOUNT_PASSWORD, {"role": "expert"}
        )
        logger.info("✅ Expert account ready: %s (%s)", expert_user.email, "created" if created else "updated")
    except Exception as exc:
        logger.warning("⚠️  Could not prepare expert account: %s", exc)

    logger.info("✅ Application startup complete")
    logger.info("=" * 70)

    yield

    # Shutdown
    logger.info("🛑 Shutting down...")
    try:
        from drone_gps import get_drone_gps  # noqa: PLC0415
        drone_gps = get_drone_gps()
        if drone_gps:
            await drone_gps.disconnect()
            logger.info("✅ Drone GPS disconnected")
    except Exception:
        pass
    logger.info("✅ Application shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    application = FastAPI(
        title="Coconut Leaf Disease Detector",
        version="2.0.0",
        description="Disease detection and inventory management",
        lifespan=lifespan,
    )

    # CORS
    application.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Cache-control for frontend assets
    @application.middleware("http")
    async def prevent_frontend_cache(request: Request, call_next):
        response = await call_next(request)
        if request.url.path in {
            "/", "/index.html", "/static/index.html", "/static/app.js",
            "/monitoring.js", "/static/monitoring.js",
        }:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @application.middleware("http")
    async def limit_memory_intensive_image_pipelines(request: Request, call_next):
        """Bound complete image pipelines so concurrent DJI uploads cannot OOM Render."""
        if not _is_memory_intensive_image_pipeline(request):
            return await call_next(request)
        try:
            await asyncio.wait_for(
                _image_pipeline_slots.acquire(), timeout=IMAGE_PIPELINE_QUEUE_SECONDS,
            )
        except TimeoutError:
            logger.warning("Image pipeline busy; rejecting request before decoding")
            return JSONResponse(
                status_code=503,
                content={"detail": "Image analysis is busy. Please retry shortly."},
                headers={"Retry-After": "10"},
            )
        try:
            return await call_next(request)
        finally:
            _image_pipeline_slots.release()

    # Routers
    application.include_router(auth.router)
    application.include_router(recommendations.router)
    application.include_router(detections.router)
    application.include_router(farm_boundaries.router)
    application.include_router(expert.router)
    application.include_router(monitoring.router)
    application.include_router(tree_identity.router)
    application.include_router(media.router)
    application.include_router(notifications.router)
    application.include_router(records.router)
    application.include_router(reports.router)
    application.include_router(drone.router)
    application.include_router(health.router)

    # API media guards must precede this mount. In particular, historic
    # /static/uploads URLs now require Firebase ownership or expert access.
    application.mount("/static", StaticFiles(directory="static"), name="static")

    # Firebase Hosting exposes the static directory as the site root.  Serving
    # it here too keeps the same asset URLs working during local development.
    # This catch-all mount must remain after every API router.
    application.mount("/", StaticFiles(directory="static", html=True), name="frontend")

    return application


app = create_app()
