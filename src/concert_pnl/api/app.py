"""Точка входа: uvicorn concert_pnl.api.app:create_app --factory

Один процесс: HTTP API + статика Mini App + бот (режим задаёт BOT_MODE).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from concert_pnl.api.routes import router
from concert_pnl.bot import webhook
from concert_pnl.bot.runner import run_polling, run_webhook
from concert_pnl.config import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    match settings.bot_mode:
        case "polling":
            async with run_polling(settings):
                yield
        case "webhook":
            async with run_webhook(settings) as (bot, dp):
                app.state.bot, app.state.dp = bot, dp
                app.state.webhook_secret = settings.webhook_secret
                yield
        case "off":
            logger.info("BOT_MODE=off, бот в этом процессе не запускается")
            yield


def create_app() -> FastAPI:
    # uvicorn настраивает только свои логгеры; без этого INFO нашего кода и aiogram
    # («Webhook зарегистрирован», «Update handled») в логах хостинга не видно
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    app = FastAPI(title="Concert P&L", lifespan=lifespan)
    app.include_router(router)
    if settings.bot_mode == "webhook":
        app.include_router(webhook.router)

    # Статику монтируем последней: маршрут "/" перехватил бы всё, что выше
    if settings.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
    else:
        logger.warning("Папка фронтенда %s не найдена, статика не раздаётся", settings.frontend_dir)
    return app
