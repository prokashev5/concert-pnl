import copy

import pytest
from fastapi.testclient import TestClient
from initdata import TOKEN, make_fields, sign

from concert_pnl.api.app import create_app
from concert_pnl.config import get_settings

# Тот же эталон, что в test_calculator.make_event
PAYLOAD = {
    "capacity": 1000,
    "comps": 50,
    "tiers": [
        {"name": "standard", "price": "2000", "quantity": 800},
        {"name": "vip", "price": "5000", "quantity": 100},
    ],
    "fixed_costs": [
        {"name": "artist", "amount": "1000000"},
        {"name": "rent", "amount": "300000"},
        {"name": "production", "amount": "200000"},
        {"name": "marketing", "amount": "150000"},
    ],
    "per_ticket_cost": "50",
    "ticketing_fee_rate": "0.10",
    "tax": {"mode": "income", "rate": "0.06"},
    "scenarios": [
        {"name": "pessimistic", "sell_through": "0.5", "probability": "0.25"},
        {"name": "base", "sell_through": "0.8", "probability": "0.5"},
        {"name": "optimistic", "sell_through": "1", "probability": "0.25"},
    ],
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    (tmp_path / "index.html").write_text("<h1>mini app</h1>", encoding="utf-8")
    monkeypatch.setenv("BOT_TOKEN", TOKEN)
    monkeypatch.setenv("WEBAPP_URL", "https://example.com")
    monkeypatch.setenv("FRONTEND_DIR", str(tmp_path))
    monkeypatch.setenv("RUN_BOT", "false")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:  # with — чтобы прогнать lifespan
        yield client
    get_settings.cache_clear()


def post(client: TestClient, payload: dict, init_data: str | None = None):
    headers = {"X-Telegram-Init-Data": init_data or sign(make_fields())}
    return client.post("/api/calculate", json=payload, headers=headers)


def payload_with(**changes) -> dict:
    payload = copy.deepcopy(PAYLOAD)
    payload.update(changes)
    return payload


class TestCalculate:
    def test_report(self, client):
        response = post(client, PAYLOAD)
        assert response.status_code == 200
        report = response.json()
        base = {s["name"]: s for s in report["scenarios"]}["base"]
        # Decimal уходит в JSON строкой — фронт не теряет копейки на float
        assert base["net_profit"] == "-274800.00"
        assert base["tickets_sold"] == 720
        assert report["break_even"] == {
            "sell_through": "0.9600",
            "tickets": 864,
            "gross_revenue": "2016000.00",
        }
        assert report["expected_net_profit"] == "-317775.00"
        assert report["zero_sales_net_profit"] == "-1650000.00"

    def test_requires_auth(self, client):
        assert client.post("/api/calculate", json=PAYLOAD).status_code == 401

    def test_rejects_bad_signature(self, client):
        assert post(client, PAYLOAD, sign(make_fields(), token="1:other")).status_code == 401


class TestValidation:
    @pytest.mark.parametrize(
        "changes",
        [
            {"per_ticket_cost": 50.5},  # float
            {"per_ticket_cost": "NaN"},
            {"per_ticket_cost": "1e13"},  # выше лимита
            {"per_ticket_cost": "0.001"},  # больше двух знаков
            {"ticketing_fee_rate": "1"},
            {"ticketing_fee_rate": "1E-25"},  # больше 6 знаков
            {
                "scenarios": [  # "1" + "1E-30" округлилось бы до суммы 1
                    {"name": "a", "sell_through": "1", "probability": "1"},
                    {"name": "b", "sell_through": "0", "probability": "1E-30"},
                ]
            },
            {"capacity": 0},
            {"tiers": []},
            {"tiers": [{"name": f"t{i}", "price": "1", "quantity": 1} for i in range(21)]},
            {"unknown_field": 1},
        ],
    )
    def test_schema_errors(self, client, changes):
        assert post(client, payload_with(**changes)).status_code == 422

    def test_usn_15_minimum_tax_by_default(self, client):
        # 100 × 1000 ₽, расходы 95 000: 15% от 5 000 = 750 < 1% от 100 000 = 1 000
        payload = payload_with(
            capacity=100,
            comps=0,
            tiers=[{"name": "all", "price": "1000", "quantity": 100}],
            fixed_costs=[{"name": "rent", "amount": "95000"}],
            per_ticket_cost="0",
            ticketing_fee_rate="0",
            tax={"mode": "income_minus_expenses", "rate": "0.15"},
            scenarios=[{"name": "full", "sell_through": "1"}],
        )
        response = post(client, payload)
        assert response.status_code == 200
        assert response.json()["scenarios"][0]["tax"] == "1000.00"

    def test_core_rule_violation(self, client):
        response = post(client, payload_with(comps=101))
        assert response.status_code == 422
        assert "вместимость" in response.json()["detail"]


class TestApp:
    def test_health_without_auth(self, client):
        assert client.get("/api/health").json() == {"status": "ok"}

    def test_serves_frontend(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "mini app" in response.text
