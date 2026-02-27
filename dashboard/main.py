from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, Awaitable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from starlette.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from shared.database import async_engine
from dashboard.routers import pages, chat


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await async_engine.dispose()


app = FastAPI(title="Nanos Dashboard", version="0.1.0", lifespan=lifespan)

static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


# Middleware: redirect to /unlock when the gateway is locked
SKIP_LOCK_PREFIXES = ("/unlock", "/reset", "/static/")


@app.middleware("http")
async def lock_check_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    path = request.url.path
    if not any(path.startswith(p) for p in SKIP_LOCK_PREFIXES):
        locked = await pages.check_gateway_lock()
        if locked:
            return RedirectResponse(url="/unlock", status_code=302)
    return await call_next(request)


app.include_router(chat.router)
app.include_router(pages.router)
