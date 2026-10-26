"""Жизненный цикл бота внутри процесса API: polling (локально) или webhook (хостинг).

Polling допускает только один процесс на токен: при нескольких воркерах uvicorn
бот должен работать ровно в одном (BOT_MODE=off в остальных) или в режиме webhook.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
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


class _StopRetrying(Exception):
    """Ошибка, которую повтор не исправит: бот выключается до перезапуска процесса."""


def _log_task_exit(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    if exc := task.exception():
        logger.error("Бот остановился с ошибкой, API продолжает работать", exc_info=exc)


def build_dispatcher(settings: Settings) -> Dispatcher:
    # Именованные аргументы диспетчера доступны хендлерам как параметры (webapp_url)
    dp = Dispatcher(webapp_url=settings.webapp_url)
    dp.include_router(create_router())
    return dp


async def _setup_menu_button(bot: Bot, webapp_url: str) -> None:
    """Кнопка меню рядом с полем ввода. Ошибка не должна мешать боту работать."""
    try:
        await asyncio.wait_for(
            bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text=OPEN_APP_TEXT, web_app=WebAppInfo(url=webapp_url))
            ),
            timeout=STARTUP_TIMEOUT_SECONDS,
        )
    except (TelegramAPIError, TimeoutError):  # включая сетевые ошибки и неверный токен
        logger.exception("Не удалось установить кнопку меню")


async def _retry_forever(name: str, attempt: Callable[[], Awaitable[None]]) -> None:
    """Повторяет attempt с растущей паузой, пока она не завершится без ошибки.

    Сеть при старте на хостинге бывает недоступна; aiogram такие сбои сам не повторяет.
    """
    loop = asyncio.get_running_loop()
    delay = RETRY_MIN_SECONDS
    while True:
        started = loop.time()
        try:
            await attempt()
            return
        except TelegramUnauthorizedError:
            logger.error("Telegram отклонил BOT_TOKEN, бот выключен до перезапуска процесса")
            return
        except _StopRetrying as e:
            logger.error("%s", e)
            return
        except Exception:
            if loop.time() - started > RETRY_MAX_SECONDS:
                delay = RETRY_MIN_SECONDS  # долго работал — сбой не подряд
            logger.exception("%s: сбой, повтор через %s с", name, delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, RETRY_MAX_SECONDS)


async def _stop_task(
    task: asyncio.Task[None], graceful: Callable[[], Awaitable[None]] | None = None
) -> None:
    if not task.done() and graceful is not None:
        try:
            await graceful()
        except RuntimeError:
            pass  # мягко останавливать нечего: polling не запущен (пауза перед повтором)
        else:
            # Даём доработать текущим апдейтам, но не бесконечно
            await asyncio.wait({task}, timeout=SHUTDOWN_TIMEOUT_SECONDS)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@asynccontextmanager
async def run_polling(settings: Settings) -> AsyncIterator[None]:
    bot = Bot(token=settings.bot_token.get_secret_value())
    dp = build_dispatcher(settings)

    async def attempt() -> None:
        # С висящим webhook getUpdates падает бесконечно. Снимать его сами не будем:
        # локальный запуск с боевым токеном молча сломал бы бота на хостинге
        if (await bot.get_webhook_info()).url:
            raise _StopRetrying(
                "На этом токене включён webhook — похоже, это токен бота с хостинга. "
                "Для локального запуска заведи отдельного бота в @BotFather."
            )
        await _setup_menu_button(bot, settings.webapp_url)
        # Сессию закрываем сами, иначе повторная попытка получит закрытую
        await dp.start_polling(bot, handle_signals=False, close_bot_session=False)

    # Всё общение с Telegram — в фоне: старт API от него не зависит
    task = asyncio.create_task(_retry_forever("polling", attempt), name="bot-polling")
    task.add_done_callback(_log_task_exit)
    try:
        yield
    finally:
        await _stop_task(task, dp.stop_polling)
        await bot.session.close()


@asynccontextmanager
async def run_webhook(settings: Settings) -> AsyncIterator[tuple[Bot, Dispatcher]]:
    """Регистрирует webhook и отдаёт (bot, dp) для роута, который принимает апдейты.

    При остановке webhook НЕ снимается: Render усыпляет сервис после 15 минут простоя,
    и снятый webhook значил бы, что бот больше никогда не проснётся.
    """
    bot = Bot(token=settings.bot_token.get_secret_value())
    dp = build_dispatcher(settings)
    await dp.emit_startup(bot=bot)  # в webhook-режиме это не делает за нас start_polling

    async def attempt() -> None:
        await _setup_menu_button(bot, settings.webapp_url)
        # drop_pending_updates не передаём: в очереди лежит апдейт, который нас разбудил
        await bot.set_webhook(
            url=settings.webhook_url,
            secret_token=settings.webhook_secret,
            allowed_updates=dp.resolve_used_update_types(),
        )
        logger.info("Webhook зарегистрирован: %s", settings.webhook_url)

    task = asyncio.create_task(_retry_forever("set_webhook", attempt), name="bot-webhook-setup")
    task.add_done_callback(_log_task_exit)
    try:
        yield bot, dp
    finally:
        await _stop_task(task)
        await dp.emit_shutdown(bot=bot)
        await bot.session.close()
