import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api.health import router as health_router
from .api.router import router
from .api.store import close_pool


@asynccontextmanager
async def _lifespan(_application: FastAPI) -> AsyncIterator[None]:
    if os.getenv("APP_ENV") == "production":
        secret = os.getenv("AUTH_JWT_SECRET", "")
        if len(secret) < 32:
            raise RuntimeError("Production requires AUTH_JWT_SECRET with at least 32 characters")
    yield
    await close_pool()


def create_app() -> FastAPI:
    application = FastAPI(title="AI Travel Agent API", version="1.0", lifespan=_lifespan)
    application.include_router(health_router)
    application.include_router(router)

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _error: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "Некорректные параметры запроса"})

    return application


app = create_app()
