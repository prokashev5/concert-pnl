import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.types import Update
from initdata import TOKEN
from pydantic import SecretStr, ValidationError

from concert_pnl.bot import runner
from concert_pnl.bot.handlers import OPEN_APP_TEXT, create_router, start
from concert_pnl.config import Settings

URL = "https://example.com"


def make_settings(**overrides) -> Settings:
    params = {"bot_token": SecretStr(TOKEN), "webapp_url": URL, **overrides}
    return Settings(_env_file=None, **params)


class TestHandlers:
    def test_start_sends_inline_webapp_button(self):
        message = AsyncMock()
        asyncio.run(start(message, webapp_url=URL))

        markup = message.answer.await_args.kwargs["reply_markup"]
        button = markup.inline_keyboard[0][0]
        assert button.text == OPEN_APP_TEXT
        assert button.web_app.url == URL


class TestRouting:
    @staticmethod
    def feed_start(monkeypatch, chat_type: str) -> AsyncMock:
        sent = AsyncMock()
        monkeypatch.setattr(Bot, "__call__", sent)  # перехватываем все запросы к Bot API
        dp = Dispatcher(webapp_url=URL)
        dp.include_router(create_router())
        update = Update.model_validate(
            {
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "date": 0,
                    "chat": {"id": 42, "type": chat_type},
                    "from": {"id": 42, "is_bot": False, "first_name": "Иван"},
                    "text": "/start",
                    "entities": [{"type": "bot_command", "offset": 0, "length": 6}],
                },
            }
        )
        asyncio.run(dp.feed_update(Bot(token=TOKEN), update))
        return sent

    def test_start_in_private_chat(self, monkeypatch):
        sent = self.feed_start(monkeypatch, "private")
        method = sent.await_args.args[0]
        assert method.reply_markup.inline_keyboard[0][0].web_app.url == URL

    def test_ignored_in_groups(self, monkeypatch):
        assert not self.feed_start(monkeypatch, "group").await_count


class TestSettings:
    def test_webapp_url_must_be_https(self):
        with pytest.raises(ValidationError, match="https"):
            make_settings(webapp_url="http://localhost:8000")

    def test_bot_token_format_checked_at_startup(self):
        with pytest.raises(ValidationError, match="BotFather"):
            make_settings(bot_token=SecretStr("not a token"))


def network_error() -> TelegramNetworkError:
    return TelegramNetworkError(method=MagicMock(), message="down")


class FakePolling:
    """Подмена Dispatcher.start_polling: по очереди бросает заданные ошибки, потом висит до отмены."""

    def __init__(self, failures: list[Exception] | None = None):
        self.failures = list(failures or [])
        self.calls = 0
        self.stopped = False
        self.running = asyncio.Event()

    async def __call__(self, dp, *bots, **kwargs):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        self.running.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.stopped = True
            raise


class TestRunner:
    @pytest.fixture
    def menu(self, monkeypatch):
        menu = AsyncMock()
        monkeypatch.setattr(Bot, "set_chat_menu_button", menu)
        monkeypatch.setattr(runner, "RETRY_MIN_SECONDS", 0)
        return menu

    @staticmethod
    def run(monkeypatch, polling: FakePolling, until_running: bool = True) -> None:
        monkeypatch.setattr(Dispatcher, "start_polling", polling)

        async def scenario():
            async with runner.run_bot(make_settings()):
                if until_running:
                    await asyncio.wait_for(polling.running.wait(), timeout=2)
                else:
                    await asyncio.sleep(0.05)

        asyncio.run(asyncio.wait_for(scenario(), timeout=5))

    def test_starts_and_stops_cleanly(self, monkeypatch, menu):
        polling = FakePolling()
        self.run(monkeypatch, polling)
        assert polling.stopped
        assert menu.await_args.kwargs["menu_button"].web_app.url == URL

    def test_menu_button_failure_does_not_block_polling(self, monkeypatch, menu):
        menu.side_effect = network_error()
        polling = FakePolling()
        self.run(monkeypatch, polling)
        assert polling.stopped

    def test_restarts_after_network_failure_on_startup(self, monkeypatch, menu):
        # aiogram не повторяет стартовый getMe — повторяем мы
        polling = FakePolling(failures=[network_error(), network_error()])
        self.run(monkeypatch, polling)
        assert polling.calls == 3
        assert polling.stopped

    def test_gives_up_on_invalid_token(self, monkeypatch, menu):
        polling = FakePolling(
            failures=[TelegramUnauthorizedError(method=MagicMock(), message="Unauthorized")]
        )
        self.run(monkeypatch, polling, until_running=False)
        assert polling.calls == 1

    def test_shutdown_during_retry_pause_is_fast(self, monkeypatch, menu):
        monkeypatch.setattr(runner, "RETRY_MIN_SECONDS", 30)
        polling = FakePolling(failures=[network_error()])
        self.run(monkeypatch, polling, until_running=False)  # упадёт по таймауту 5 с, если ждём паузу
        assert polling.calls == 1
