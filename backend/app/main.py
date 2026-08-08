"""FastAPI application factory and ASGI entrypoint."""

import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import close_all_sessions

from app.api.admin import login_router as admin_login_router
from app.api.admin import router as admin_router
from app.api.engines import router as engines_router
from app.api.gitlab_webhook import router as gitlab_webhook_router
from app.api.health import router as health_router
from app.api.reviews import router as reviews_router
from app.api.stats import router as stats_router
from app.core.config import get_settings, validate_secret_key
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
    # 启动即校验 SECRET_KEY：缺失或非法时直接拒绝启动，避免运行时加密才报错。
    validate_secret_key(settings)
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
    app.include_router(stats_router)

    # ---- 静态文件托管（单包部署模式）----
    # 前端构建产物被打包进 app/static/ 时，挂载为静态资源并提供 SPA fallback。
    # 纯后端部署（static 目录不存在）时跳过，不影响 API 服务。
    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        # SPA fallback：未匹配到 API/文档/静态资源的路径，返回 index.html 让前端路由接管
        @app.exception_handler(404)
        async def _spa_fallback_handler(
            request: Request, _exc: HTTPException
        ) -> Response:
            path = request.url.path
            # API / 健康检查 / OpenAPI 文档路径直接返回标准 JSON 404
            api_prefixes = ("/api", "/health", "/docs", "/openapi.json", "/redoc")
            if any(path.startswith(p) for p in api_prefixes):
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            # 带扩展名的请求（静态资源）也直接 404，避免 fallback 到 HTML
            last_segment = path.rsplit("/", 1)[-1]
            if "." in last_segment:
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            index_path = static_dir / "index.html"
            if index_path.is_file():
                return FileResponse(str(index_path))
            return JSONResponse(status_code=404, content={"detail": "Not Found"})

        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


app = create_app()
