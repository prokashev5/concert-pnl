"""Аутентификация запросов Mini App по initData.

Подпись проверяет aiogram (HMAC-SHA256, секрет = HMAC("WebAppData", bot_token)).
Сверху проверяем свежесть auth_date и наличие пользователя.
Фронтенд передаёт Telegram.WebApp.initData как есть в заголовке X-Telegram-Init-Data.
"""

import logging
import time
from typing import Annotated

from aiogram.utils.web_app import WebAppInitData, WebAppUser, safe_parse_webapp_init_data
from fastapi import Depends, Header, HTTPException, status

from concert_pnl.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Допуск на расхождение часов между серверами Telegram и нашим
CLOCK_SKEW_SECONDS = 60


class InitDataError(Exception):
    pass


def validate_init_data(
    init_data: str, bot_token: str, ttl_seconds: int, now: float | None = None
) -> WebAppInitData:
    if not init_data:
        raise InitDataError("initData отсутствует")
    try:
        data = safe_parse_webapp_init_data(bot_token, init_data)
    except ValueError as e:  # неверная подпись, битая строка или невалидные поля
        raise InitDataError("Неверная подпись или формат initData") from e

    age = (time.time() if now is None else now) - data.auth_date.timestamp()
    if age > ttl_seconds:
        raise InitDataError("initData устарела, переоткройте приложение")
    if age < -CLOCK_SKEW_SECONDS:
        raise InitDataError("auth_date из будущего")
    if data.user is None:
        raise InitDataError("В initData нет пользователя")
    return data


def get_init_data(
    settings: Annotated[Settings, Depends(get_settings)],
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> WebAppInitData:
    try:
        return validate_init_data(
            x_telegram_init_data or "",
            settings.bot_token.get_secret_value(),
            settings.init_data_ttl_seconds,
        )
    except InitDataError as e:
        logger.warning("Отклонён запрос Mini App: %s", e)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e


def get_current_user(
    init_data: Annotated[WebAppInitData, Depends(get_init_data)],
) -> WebAppUser:
    return init_data.user
