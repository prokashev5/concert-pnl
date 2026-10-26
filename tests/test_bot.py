import asyncio
import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.methods import DeleteWebhook, SendMessage, SetWebhook
from aiogram.types import Update
from fastapi.testclient import TestClient
from initdata import TOKEN
from pydantic import SecretStr, ValidationError

from concert_pnl.api.app import create_app
from concert_pnl.bot import runner
from concert_pnl.bot.handlers import OPEN_APP_TEXT, create_router, start
from concert_pnl.config import WEBHOOK_PATH, Settings, get_settings

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

    def test_render_external_url_is_used_as_webapp_url(self, monkeypatch):
        monkeypatch.delenv("WEBAPP_URL", raising=False)
        monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://concert.onrender.com/")
        settings = Settings(_env_file=None, bot_token=SecretStr(TOKEN))
        assert settings.webapp_url == "https://concert.onrender.com"
        assert settings.webhook_url == "https://concert.onrender.com/tg/webhook"

    @pytest.mark.parametrize(("render", "expected"), [("true", "webhook"), (None, "polling")])
    def test_default_bot_mode_depends_on_render(self, monkeypatch, render, expected):
        monkeypatch.delenv("BOT_MODE", raising=False)
        if render is None:
            monkeypatch.delenv("RENDER", raising=False)
        else:
            monkeypatch.setenv("RENDER", render)
        assert make_settings().bot_mode == expected

    def test_webhook_secret_fits_telegram_rules_and_is_stable(self):
        secret = make_settings().webhook_secret
        assert re.fullmatch(r"[A-Za-z0-9_-]{1,256}", secret)
        assert secret == make_settings().webhook_secret
        assert secret != make_settings(bot_token=SecretStr("999:other")).webhook_secret
        assert TOKEN not in secret


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


class TestPolling:
    @pytest.fixture
    def webhook_info(self, monkeypatch):
        info = AsyncMock(return_value=MagicMock(url=""))
        monkeypatch.setattr(Bot, "get_webhook_info", info)
        return info

    @pytest.fixture
    def menu(self, monkeypatch, webhook_info):
        menu = AsyncMock()
        monkeypatch.setattr(Bot, "set_chat_menu_button", menu)
        monkeypatch.setattr(runner, "RETRY_MIN_SECONDS", 0)
        return menu

    @staticmethod
    def run(monkeypatch, polling: FakePolling, until_running: bool = True) -> None:
        monkeypatch.setattr(Dispatcher, "start_polling", polling)

        async def scenario():
            async with runner.run_polling(make_settings()):
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

    def test_refuses_token_with_webhook(self, monkeypatch, menu, webhook_info):
        # Локальный запуск с боевым токеном не должен трогать webhook на хостинге
        webhook_info.return_value = MagicMock(url="https://prod.onrender.com/tg/webhook")
        delete_webhook = AsyncMock()
        monkeypatch.setattr(Bot, "delete_webhook", delete_webhook)
        polling = FakePolling()
        self.run(monkeypatch, polling, until_running=False)
        assert polling.calls == 0
        assert not delete_webhook.await_count


def telegram_calls(api: AsyncMock, method_type: type) -> list:
    return [call.args[0] for call in api.await_args_list if isinstance(call.args[0], method_type)]


@pytest.fixture
def bot_api(monkeypatch):
    """Перехватывает все запросы к Bot API."""
    api = AsyncMock(return_value=True)
    monkeypatch.setattr(Bot, "__call__", api)
    return api


class TestWebhookLifecycle:
    def test_registers_webhook_and_keeps_it_on_shutdown(self, bot_api):
        settings = make_settings(bot_mode="webhook")

        async def scenario():
            async with runner.run_webhook(settings):
                for _ in range(100):
                    if telegram_calls(bot_api, SetWebhook):
                        break
                    await asyncio.sleep(0.01)

        asyncio.run(scenario())
        [set_webhook] = telegram_calls(bot_api, SetWebhook)
        assert set_webhook.url == f"{URL}{WEBHOOK_PATH}"
        assert set_webhook.secret_token == settings.webhook_secret
        # Апдейт, разбудивший сервис, лежит в очереди — выкидывать его нельзя
        assert not set_webhook.drop_pending_updates
        # Засыпание на Render — обычная остановка; снятый webhook = бот умер навсегда
        assert not telegram_calls(bot_api, DeleteWebhook)


START_UPDATE = {
    "update_id": 1,
    "message": {
        "message_id": 1,
        "date": 0,
        "chat": {"id": 42, "type": "private"},
        "from": {"id": 42, "is_bot": False, "first_name": "Иван"},
        "text": "/start",
        "entities": [{"type": "bot_command", "offset": 0, "length": 6}],
    },
}


class TestWebhookRoute:
    @pytest.fixture
    def client(self, monkeypatch, bot_api):
        monkeypatch.setenv("BOT_TOKEN", TOKEN)
        monkeypatch.setenv("WEBAPP_URL", URL)
        monkeypatch.setenv("BOT_MODE", "webhook")
        get_settings.cache_clear()
        with TestClient(create_app()) as client:
            yield client
        get_settings.cache_clear()

    @staticmethod
    def post(client, secret: str | None):
        headers = {} if secret is None else {"X-Telegram-Bot-Api-Secret-Token": secret}
        return client.post(WEBHOOK_PATH, json=START_UPDATE, headers=headers)

    def test_start_is_answered(self, client, bot_api):
        response = self.post(client, get_settings().webhook_secret)
        assert response.status_code == 200
        [message] = telegram_calls(bot_api, SendMessage)
        assert message.reply_markup.inline_keyboard[0][0].web_app.url == URL

    @pytest.mark.parametrize("secret", [None, "", "wrong"])
    def test_rejects_wrong_secret(self, client, bot_api, secret):
        assert self.post(client, secret).status_code == 401
        assert not telegram_calls(bot_api, SendMessage)

    def test_handler_error_still_returns_200(self, client, bot_api):
        # Иначе Telegram будет повторять тот же апдейт по кругу
        async def fail_on_send(method, *args, **kwargs):
            if isinstance(method, SendMessage):
                raise network_error()
            return True

        bot_api.side_effect = fail_on_send
        assert self.post(client, get_settings().webhook_secret).status_code == 200

    @pytest.mark.parametrize("mode", ["polling", "off"])
    def test_route_absent_outside_webhook_mode(self, monkeypatch, mode):
        monkeypatch.setenv("BOT_TOKEN", TOKEN)
        monkeypatch.setenv("WEBAPP_URL", URL)
        monkeypatch.setenv("BOT_MODE", mode)
        get_settings.cache_clear()
        try:
            # Без with: lifespan не запускается, настоящий polling не стартует
            response = TestClient(create_app()).post(WEBHOOK_PATH, json=START_UPDATE)
        finally:
            get_settings.cache_clear()
        assert response.status_code in (404, 405)
