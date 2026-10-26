"""Подпись initData для тестов — независимо от aiogram, по спецификации Telegram."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

TOKEN = "123456:TEST-token"
USER = {"id": 42, "first_name": "Иван", "username": "ivan"}


def sign(fields: dict[str, str], token: str = TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": signature})


def make_fields(auth_date: int | None = None, user: dict | None = USER) -> dict[str, str]:
    if auth_date is None:
        auth_date = int(time.time())
    fields = {"auth_date": str(auth_date), "query_id": "AAH-test"}
    if user is not None:
        fields["user"] = json.dumps(user, ensure_ascii=False, separators=(",", ":"))
    return fields
