from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api.router import router

def create_app() -> FastAPI:
    application = FastAPI(title="Ostrovok Trip API", version="1.0")
    application.include_router(router)

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _error: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "Некорректные параметры запроса"})

    return application


app = create_app()
