"""Точка входа: uvicorn concert_pnl.api.app:create_app --factory

Один процесс: HTTP API + статика Mini App + бот (если RUN_BOT=true).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from concert_pnl.api.routes import router
from concert_pnl.bot.runner import run_bot
from concert_pnl.config import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if not settings.run_bot:
        logger.info("RUN_BOT=false, бот в этом процессе не запускается")
        yield
        return
    async with run_bot(settings):
        yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Concert P&L", lifespan=lifespan)
    app.include_router(router)

    # Статику монтируем последней: маршрут "/" перехватил бы всё, что ниже
    if settings.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
    else:
        logger.warning("Папка фронтенда %s не найдена, статика не раздаётся", settings.frontend_dir)
    return app
