from functools import lru_cache
from pathlib import Path

from aiogram.utils.token import TokenValidationError, validate_token
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# src/concert_pnl/config.py -> корень проекта
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    bot_token: SecretStr
    webapp_url: str
    init_data_ttl_seconds: int = 86400
    # По умолчанию не зависит от каталога, из которого запущен uvicorn
    frontend_dir: Path = PROJECT_ROOT / "frontend"
    # Запускать polling бота в этом процессе. Ровно один процесс на токен.
    run_bot: bool = True

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
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
