"""FastAPI application factory and ASGI entrypoint."""

import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import close_all_sessions

from app.api.admin import login_router as admin_login_router
from app.api.admin import router as admin_router
from app.api.engines import router as engines_router
from app.api.gitlab_webhook import router as gitlab_webhook_router
from app.api.health import router as health_router
from app.api.reviews import router as reviews_router
from app.core.config import get_settings
from app.core.db import engine
from app.core.logging import configure_logging
from app.core.redis import close_redis
from app.engines import load_builtin_engines

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown resources.

    Yields:
        None while the application is running.
    """

    settings = get_settings()
    configure_logging(settings)
    load_builtin_engines()
    logger.info("Starting %s %s", settings.app_name, settings.app_version)
    try:
        yield
    finally:
        await close_redis()
        await close_all_sessions()
        await engine.dispose()
        logger.info("Application shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        FastAPI: Configured ASGI application.
    """

    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_logging_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Log incoming requests and response metadata.

        Args:
            request: Incoming HTTP request.
            call_next: Next ASGI application callable.

        Returns:
            Response: HTTP response returned by downstream handlers.
        """

        started_at = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - started_at) * 1000
        logger.info(
            "request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 2),
            },
        )
        return response

    app.include_router(health_router)
    app.include_router(engines_router)
    app.include_router(gitlab_webhook_router)
    app.include_router(reviews_router)
    app.include_router(admin_login_router)
    app.include_router(admin_router)
    return app


app = create_app()
