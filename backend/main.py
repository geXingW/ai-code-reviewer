"""FastAPI application factory and ASGI entrypoint."""

import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import close_all_sessions
from starlette.responses import FileResponse as StarletteFileResponse
from starlette.types import Scope

from api.admin import login_router as admin_login_router
from api.admin import router as admin_router
from api.engines import router as engines_router
from api.gitlab_webhook import router as gitlab_webhook_router
from api.health import router as health_router
from api.reviews import router as reviews_router
from api.stats import router as stats_router
from core.config import get_settings, validate_secret_key
from core.db import engine
from core.logging import configure_logging
from engines import load_builtin_engines

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown resources.

    Yields:
        None while the application is running.
    """

    settings = get_settings()
    # 启动即校验 SECRET_KEY：缺失或非法时直接拒绝启动，避免运行时加密才报错。
    validate_secret_key(settings)
    configure_logging(settings)
    load_builtin_engines()
    logger.info("Starting %s %s", settings.app_name, settings.app_version)
    try:
        yield
    finally:
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
    app.include_router(stats_router)

    # ---- 静态文件托管（单包部署模式）----
    # 前端构建产物被打包进 app/static/ 时，挂载为静态资源并提供 SPA fallback。
    # 纯后端部署（static 目录不存在）时跳过，不影响 API 服务。
    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount(
            "/",
            _SPAStaticFiles(directory=str(static_dir), html=True),
            name="static",
        )

    return app


class _SPAStaticFiles(StaticFiles):
    """StaticFiles with SPA fallback for single-package deployment.

    When a requested file is not found and the path does not look like an
    API / static-asset request, return ``index.html`` so the frontend
    router can handle the URL.

    This intentionally does NOT use a global 404 exception handler, which
    would clobber business-level 404 responses (e.g. "engine not
    registered") raised from API route handlers.
    """

    # Path prefixes that must never fall back to index.html.
    _API_PREFIXES = ("/api", "/health", "/docs", "/openapi.json", "/redoc")

    async def get_response(self, path: str, scope: Scope) -> Response:
        """Return static file, or index.html for SPA routes on miss."""

        response = await super().get_response(path, scope)
        if response.status_code != 404:
            return response

        # Don't fall back for API / health / docs paths.
        if any(path.startswith(p) for p in self._API_PREFIXES):
            return response

        # Don't fall back for paths with a file extension (static assets).
        last_segment = path.rsplit("/", 1)[-1]
        if "." in last_segment:
            return response

        # SPA fallback: serve index.html
        full_path, stat_result = self.lookup_path("index.html")
        if not full_path or stat_result is None:
            return response
        return StarletteFileResponse(full_path)


app = create_app()
