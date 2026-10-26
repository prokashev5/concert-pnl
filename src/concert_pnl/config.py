import hashlib
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from aiogram.utils.token import TokenValidationError, validate_token
from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# src/concert_pnl/config.py -> корень проекта (при установке через pip install -e .)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Telegram шлёт апдейты POST-запросом сюда (в режиме webhook)
WEBHOOK_PATH = "/tg/webhook"


class Settings(BaseSettings):
    # extra="ignore": устаревшие ключи в чужом .env (например, RUN_BOT) не валят запуск
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    bot_token: SecretStr
    # На Render адрес сервиса приходит сам в RENDER_EXTERNAL_URL
    webapp_url: str = Field(validation_alias=AliasChoices("WEBAPP_URL", "RENDER_EXTERNAL_URL"))
    init_data_ttl_seconds: int = 86400
    # По умолчанию не зависит от каталога, из которого запущен uvicorn
    frontend_dir: Path = PROJECT_ROOT / "frontend"
    # polling — локально через туннель; webhook — на хостинге, который засыпает (Render);
    # off — бот в этом процессе не работает (тесты, лишние воркеры).
    # На Render (там всегда RENDER=true) по умолчанию webhook: polling там засыпает навсегда
    bot_mode: Literal["polling", "webhook", "off"] = Field(
        default_factory=lambda: "webhook" if os.environ.get("RENDER") == "true" else "polling"
    )

    @field_validator("bot_token")
    @classmethod
    def _valid_token_format(cls, value: SecretStr) -> SecretStr:
        try:
            validate_token(value.get_secret_value())
        except TokenValidationError as e:
            raise ValueError("BOT_TOKEN не похож на токен от @BotFather") from e
        return value

    @field_validator("webapp_url")
    @classmethod
    def _require_https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("Telegram открывает Mini App только по https://")
        return value.rstrip("/")

    @property
    def webhook_url(self) -> str:
        return self.webapp_url + WEBHOOK_PATH

    @property
    def webhook_secret(self) -> str:
        """Секрет для заголовка X-Telegram-Bot-Api-Secret-Token.

        Выводится из токена: отдельную переменную не нужно заводить и не с чем рассинхронизировать.
        sha256 в hex укладывается в требования Telegram (1–256 символов A-Za-z0-9_-).
        """
        digest = hashlib.sha256(b"webhook:" + self.bot_token.get_secret_value().encode())
        return digest.hexdigest()


@lru_cache
def get_settings() -> Settings:
    return Settings()
