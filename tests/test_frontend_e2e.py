"""E2E фронта: настоящий uvicorn + системный Edge через Playwright.

Telegram SDK подменяется заглушкой с подписанной initData — сервер проверяет её по-настоящему.
Пропускается, если нет playwright или Edge.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from initdata import TOKEN, make_fields, sign

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[1]
NBSP = " "

SDK_STUB = """
window.__main = { text: null, visible: false, progress: false, click: null };
window.Telegram = { WebApp: {
  initData: %s,
  ready() {}, expand() {},
  MainButton: {
    setText(t) { __main.text = t; }, show() { __main.visible = true; },
    showProgress() { __main.progress = true; }, hideProgress() { __main.progress = false; },
    onClick(cb) { if (__main.click) throw new Error('onClick зарегистрирован дважды'); __main.click = cb; },
  },
}};
%s
"""

DARK_THEME = """
const s = document.documentElement.style;
s.setProperty('--tg-theme-secondary-bg-color', '#0e1621');
s.setProperty('--tg-theme-section-bg-color', '#17212b');
s.setProperty('--tg-theme-text-color', '#f5f5f5');
s.setProperty('--tg-theme-hint-color', '#708499');
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base_url(tmp_path_factory):
    port = free_port()
    env = {
        **os.environ,
        "BOT_TOKEN": TOKEN,
        "WEBAPP_URL": "https://example.com",
        "RUN_BOT": "false",
        "PYTHONPATH": str(ROOT / "src"),
    }
    env.pop("FRONTEND_DIR", None)
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "concert_pnl.api.app:create_app", "--factory",
         "--port", str(port), "--log-level", "warning"],
        # Не из корня проекта: путь к фронту не должен зависеть от каталога запуска
        cwd=tmp_path_factory.mktemp("cwd"),
        env=env,
    )
    url = f"http://127.0.0.1:{port}/"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(url + "api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("uvicorn не поднялся")
        yield url
    finally:
        server.terminate()
        server.wait(timeout=10)


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="msedge", headless=True)
        except sync_api.Error as e:
            pytest.skip(f"Edge недоступен: {e.message.splitlines()[0]}")
        yield browser
        browser.close()


@pytest.fixture
def open_app(browser, base_url):
    """Открывает Mini App; после теста проверяет, что в консоли не было ошибок JS."""
    contexts, errors = [], []

    def open_(init_data: str | None = None, dark: bool = False):
        init_data = sign(make_fields()) if init_data is None else init_data
        context = browser.new_context(viewport={"width": 390, "height": 844})
        stub = SDK_STUB % (json.dumps(init_data), DARK_THEME if dark else "")
        context.route(
            "https://telegram.org/js/telegram-web-app.js",
            lambda route: route.fulfill(body=stub, content_type="application/javascript"),
        )
        contexts.append(context)
        page = context.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        # Сетевые ответы 4xx браузер сам пишет в консоль — это не ошибки нашего JS
        page.on(
            "console",
            lambda m: m.type == "error"
            and not m.text.startswith("Failed to load resource")
            and errors.append(m.text),
        )
        page.goto(base_url)
        return page

    yield open_
    for context in contexts:
        context.close()
    assert not errors, f"ошибки JS: {errors}"


def fill_reference(page):
    """Эталон из test_calculator.make_event, с пробелами и запятыми, как вводит человек."""
    page.fill('[data-path="capacity"]', "1000")
    page.fill('[data-path="comps"]', "50")
    standard = page.locator("#tiers .row").nth(0)
    standard.locator('[data-field="name"]').fill("Стандарт")
    standard.locator('[data-field="price"]').fill("2 000")
    standard.locator('[data-field="quantity"]').fill("800")
    page.click("#add-tier")
    vip = page.locator("#tiers .row").nth(1)
    vip.locator('[data-field="name"]').fill("VIP")
    vip.locator('[data-field="price"]').fill("5000,00")
    vip.locator('[data-field="quantity"]').fill("100")
    for i, amount in enumerate(["1000000", "300000", "200000", "150000"]):
        page.locator("#costs .row").nth(i).locator('[data-field="amount"]').fill(amount)
    page.fill('[data-path="per_ticket_cost"]', "50")
    page.fill('[data-path="ticketing_fee_rate"]', "10")
    for i, probability in enumerate(["25", "50", "25"]):
        page.locator("#scenarios .row").nth(i).locator('[data-field="probability"]').fill(probability)


def press_main_button(page):
    page.evaluate("window.__main.click()")


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("parseMoney('1 500,5')", "1500.5"),
        ("parseMoney('0.001')", None),
        ("parseMoney('1000000000000')", None),
        ("parseMoney('-5')", None),
        ("parseCount('1000001')", None),
        ("parseCount('12')", 12),
        ("parsePercent('10,5')", {"units": 105000, "share": "0.105"}),
        ("parsePercent('100')", {"units": 1000000, "share": "1"}),
        ("parsePercent('33.3333')", {"units": 333333, "share": "0.333333"}),
        ("parsePercent('12.34567')", None),
        ("formatMoney('-274800.00')", f"−274{NBSP}800{NBSP}₽"),
        ("formatMoney('1234.50')", f"1{NBSP}234,50{NBSP}₽"),
        ("formatMoney('-0.00')", f"0{NBSP}₽"),
        ("formatShare('0.9600')", f"96{NBSP}%"),
        ("formatShare('-1.3513')", f"−135,13{NBSP}%"),
        ("formatShare('1')", f"100{NBSP}%"),
        ("formatShare('0.0005')", f"0,05{NBSP}%"),
        ("isNegative('-0.00')", False),
        ("isNegative('-0.01')", True),
        ("[1, 2, 5, 11, 21, 22, 112].map(n => plural(n, ['билет', 'билета', 'билетов']))",
         ["билет", "билета", "билетов", "билетов", "билет", "билета", "билетов"]),
    ],
)
def test_string_number_helpers(open_app, expression, expected):
    assert open_app().evaluate(expression) == expected


def test_probabilities_sum_exactly_without_float(open_app):
    page = open_app()
    assert page.evaluate(
        "['33,33', '33,33', '33,34'].map(parsePercent).reduce((a, p) => a + p.units, 0) === PERCENT_UNITS"
    )


def test_telegram_wiring(open_app):
    page = open_app()
    main_button = page.evaluate("window.__main")
    assert main_button["visible"] and main_button["text"] == "Рассчитать"
    assert page.is_hidden("#fallback-submit")
    assert page.is_hidden("#outside-banner")


def test_reference_calculation(open_app):
    page = open_app()
    fill_reference(page)
    assert "50 мест" in page.inner_text("#seats-hint")
    assert "100" in page.inner_text("#prob-hint")

    press_main_button(page)
    expect(page.locator("#results")).to_be_visible(timeout=5000)

    break_even = page.inner_text("#break-even")
    assert f"864 билета · 96{NBSP}%" in break_even
    assert f"2{NBSP}016{NBSP}000{NBSP}₽" in break_even
    summary = page.inner_text("#summary")
    assert f"−317{NBSP}775{NBSP}₽" in summary
    assert f"−1{NBSP}650{NBSP}000{NBSP}₽" in summary

    cards = page.locator(".scenario-card")
    assert cards.count() == 3
    base = cards.nth(1).inner_text()
    assert f"−274{NBSP}800{NBSP}₽" in base
    assert f"−20{NBSP}%" in base
    assert "Убыток" in base
    optimistic = cards.nth(2).inner_text()
    assert f"69{NBSP}000{NBSP}₽" in optimistic
    assert "Убыток" not in optimistic
    assert page.evaluate("window.__main.progress") is False


def test_dark_theme(open_app):
    page = open_app(dark=True)
    fill_reference(page)
    press_main_button(page)
    expect(page.locator("#results")).to_be_visible(timeout=5000)


def test_draft_survives_reload(open_app):
    page = open_app()
    fill_reference(page)
    page.reload()
    assert page.input_value('[data-path="capacity"]') == "1000"
    assert page.locator("#tiers .row").count() == 2
    assert page.locator("#tiers .row").nth(1).locator('[data-field="price"]').input_value() == "5000,00"


@pytest.mark.parametrize(
    "draft",
    [
        "{not json",
        '{"tiers": [null], "costs": [7], "scenarios": "x", "capacity": {"a": 1}}',
    ],
)
def test_broken_draft_does_not_break_app(open_app, draft):
    page = open_app()
    page.evaluate(f"localStorage.setItem('concert-pnl:form:v1', {json.dumps(draft)})")
    page.reload()
    assert page.locator("#scenarios .row").count() == 3
    assert page.evaluate("window.__main.visible")  # init дошёл до конца


def test_results_marked_stale_after_edit_and_hidden_after_error(open_app):
    page = open_app()
    fill_reference(page)
    press_main_button(page)
    expect(page.locator("#results")).to_be_visible(timeout=5000)

    page.fill('[data-path="per_ticket_cost"]', "60")
    expect(page.locator("#results")).to_have_class("stale")

    page.fill('[data-path="capacity"]', "10")
    press_main_button(page)
    expect(page.locator("#results")).to_be_hidden()


def test_client_validation(open_app):
    page = open_app()
    fill_reference(page)
    page.fill('[data-path="capacity"]', "900")
    page.locator("#tiers .row").nth(1).locator('[data-field="price"]').fill("50.555")
    page.locator("#scenarios .row").nth(2).locator('[data-field="probability"]').fill("")
    press_main_button(page)

    field_errors = page.locator(".field-error").all_inner_texts()
    assert any("вместимости" in e for e in field_errors)
    assert any("Цена" in e for e in field_errors)
    assert "у всех сценариев" in page.inner_text("#form-errors")
    assert page.is_hidden("#results")


def test_server_422_paths_are_mapped_to_form_rows(open_app):
    mapped = open_app().evaluate(
        """errorsFrom422(
            [{loc: ['body', 'fixed_costs', 0, 'amount'], msg: 'x'}, {loc: ['body', 'tax', 'rate'], msg: 'y'}],
            {fixed_costs: [3]})"""
    )
    assert [e["path"] for e in mapped] == ["fixed_costs.3.amount", "tax.rate"]


def test_unauthorized(open_app):
    page = open_app(init_data=sign(make_fields(), token="1:other"))
    fill_reference(page)
    press_main_button(page)
    expect(page.locator("#form-errors")).to_be_visible(timeout=5000)
    assert "через бота" in page.inner_text("#form-errors")
    assert page.is_hidden("#results")


def test_outside_telegram(open_app):
    page = open_app(init_data="")
    assert page.is_visible("#outside-banner")
    assert page.is_visible("#fallback-submit")
