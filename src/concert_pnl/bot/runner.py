"""Запуск бота (long polling) фоновой задачей внутри процесса API.

Polling допускает только один процесс на токен: при нескольких воркерах uvicorn
бот должен работать ровно в одном (RUN_BOT=false в остальных) или переехать на webhook.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError, TelegramUnauthorizedError
from aiogram.types import MenuButtonWebApp, WebAppInfo

from concert_pnl.bot.handlers import OPEN_APP_TEXT, create_router
from concert_pnl.config import Settings

logger = logging.getLogger(__name__)

STARTUP_TIMEOUT_SECONDS = 10
SHUTDOWN_TIMEOUT_SECONDS = 10
RETRY_MIN_SECONDS = 1
RETRY_MAX_SECONDS = 60


def _log_polling_exit(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    if exc := task.exception():
        logger.error("Бот остановился с ошибкой, API продолжает работать", exc_info=exc)


async def _setup_menu_button(bot: Bot, webapp_url: str) -> None:
    """Кнопка меню рядом с полем ввода. Ошибка не должна мешать старту API."""
    try:
        await asyncio.wait_for(
            bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text=OPEN_APP_TEXT, web_app=WebAppInfo(url=webapp_url))
            ),
            timeout=STARTUP_TIMEOUT_SECONDS,
        )
    except (TelegramAPIError, TimeoutError):  # включая сетевые ошибки и неверный токен
        logger.exception("Не удалось установить кнопку меню")


async def _poll_forever(dp: Dispatcher, bot: Bot, webapp_url: str) -> None:
    """Polling с перезапуском: aiogram повторяет только getUpdates, а сбой стартового
    getMe (сеть недоступна при деплое) завершает start_polling насовсем."""
    loop = asyncio.get_running_loop()
    delay = RETRY_MIN_SECONDS
    while True:
        await _setup_menu_button(bot, webapp_url)
        started = loop.time()
        try:
            # Сессию закрываем сами в run_bot, иначе повторная попытка получит закрытую
            await dp.start_polling(bot, handle_signals=False, close_bot_session=False)
            return  # штатная остановка через stop_polling
        except TelegramUnauthorizedError:
            logger.error("Telegram отклонил BOT_TOKEN, бот выключен до перезапуска процесса")
            return
        except Exception:
            if loop.time() - started > RETRY_MAX_SECONDS:
                delay = RETRY_MIN_SECONDS  # долго работал — сбой не подряд
            logger.exception("Бот упал, перезапуск через %s с", delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, RETRY_MAX_SECONDS)


@asynccontextmanager
async def run_bot(settings: Settings) -> AsyncIterator[None]:
    bot = Bot(token=settings.bot_token.get_secret_value())
    # Именованные аргументы диспетчера доступны хендлерам как параметры (webapp_url)
    dp = Dispatcher(webapp_url=settings.webapp_url)
    dp.include_router(create_router())

    # Всё общение с Telegram — в фоне: старт API от него не зависит
    task = asyncio.create_task(_poll_forever(dp, bot, settings.webapp_url), name="bot-polling")
    task.add_done_callback(_log_polling_exit)
    try:
        yield
    finally:
        if not task.done():
            try:
                await dp.stop_polling()
            except RuntimeError:  # polling сейчас не запущен: пауза перед повтором или старт
                task.cancel()
            else:
                # Даём доработать текущим апдейтам, но не бесконечно
                await asyncio.wait({task}, timeout=SHUTDOWN_TIMEOUT_SECONDS)
                task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await bot.session.close()
