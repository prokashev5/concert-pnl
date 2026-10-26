import time
from urllib.parse import urlencode

import pytest
from aiogram.utils.web_app import WebAppUser
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from initdata import TOKEN, USER, sign
from initdata import make_fields as _make_fields
from pydantic import SecretStr

from concert_pnl.api.security import (
    CLOCK_SKEW_SECONDS,
    InitDataError,
    get_current_user,
    validate_init_data,
)
from concert_pnl.config import Settings, get_settings

TTL = 3600
NOW = 1_800_000_000


def make_fields(auth_date: int = NOW, user: dict | None = USER) -> dict[str, str]:
    return _make_fields(auth_date, user)


def validate(init_data: str, now: int = NOW):
    return validate_init_data(init_data, TOKEN, TTL, now=now)


class TestValidateInitData:
    def test_valid(self):
        data = validate(sign(make_fields()))
        assert data.user.id == 42
        assert data.user.first_name == "Иван"

    def test_wrong_token(self):
        with pytest.raises(InitDataError, match="подпись"):
            validate(sign(make_fields(), token="999:other"))

    def test_tampered_user(self):
        init_data = sign(make_fields()).replace("%3A42", "%3A43")
        with pytest.raises(InitDataError, match="подпись"):
            validate(init_data)

    def test_missing_hash(self):
        with pytest.raises(InitDataError, match="подпись"):
            validate(urlencode(make_fields()))

    @pytest.mark.parametrize("init_data", ["", "garbage", "a=1&&&b"])
    def test_malformed(self, init_data):
        with pytest.raises(InitDataError):
            validate(init_data)

    def test_expired(self):
        with pytest.raises(InitDataError, match="устарела"):
            validate(sign(make_fields(auth_date=NOW - TTL - 1)))

    def test_ttl_boundary_is_valid(self):
        assert validate(sign(make_fields(auth_date=NOW - TTL)))

    def test_small_clock_skew_is_tolerated(self):
        assert validate(sign(make_fields(auth_date=NOW + CLOCK_SKEW_SECONDS)))

    def test_from_future(self):
        with pytest.raises(InitDataError, match="будущего"):
            validate(sign(make_fields(auth_date=NOW + CLOCK_SKEW_SECONDS + 1)))

    def test_user_required(self):
        with pytest.raises(InitDataError, match="пользователя"):
            validate(sign(make_fields(user=None)))


class TestDependency:
    @pytest.fixture
    def client(self):
        app = FastAPI()

        @app.get("/me")
        def me(user: WebAppUser = Depends(get_current_user)):
            return {"id": user.id}

        app.dependency_overrides[get_settings] = lambda: Settings(
            bot_token=SecretStr(TOKEN), webapp_url="https://example.com", init_data_ttl_seconds=TTL
        )
        return TestClient(app)

    def test_authorized(self, client):
        init_data = sign(make_fields(auth_date=int(time.time())))
        response = client.get("/me", headers={"X-Telegram-Init-Data": init_data})
        assert response.status_code == 200
        assert response.json() == {"id": 42}

    def test_missing_header(self, client):
        assert client.get("/me").status_code == 401

    def test_invalid_header(self, client):
        response = client.get("/me", headers={"X-Telegram-Init-Data": sign(make_fields(), "1:x")})
        assert response.status_code == 401
