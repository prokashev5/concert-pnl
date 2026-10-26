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

# CloudStorage в памяти; переживает reload (лежит в localStorage контекста браузера).
# Callback — через setTimeout, как у настоящего SDK: синхронная заглушка спрятала бы гонки
CLOUD_STUB = """
(() => {
  const KEY = '__cloud_store';
  const read = () => JSON.parse(localStorage.getItem(KEY) || '{}');
  const write = (s) => localStorage.setItem(KEY, JSON.stringify(s));
  const later = (cb, ...args) => cb && setTimeout(() => cb(...args), 0);
  Telegram.WebApp.isVersionAtLeast = () => true;
  Telegram.WebApp.CloudStorage = {
    setItem(k, v, cb) { const s = read(); s[k] = v; write(s); later(cb, null, true); },
    getItems(keys, cb) { const s = read(); const r = {}; keys.forEach((k) => { r[k] = s[k] ?? ''; }); later(cb, null, r); },
    getKeys(cb) { later(cb, null, Object.keys(read())); },
    removeItem(k, cb) { const s = read(); delete s[k]; write(s); later(cb, null, true); },
  };
})();
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
        "BOT_MODE": "off",
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

    def open_(init_data: str | None = None, dark: bool = False, cloud: bool = False):
        init_data = sign(make_fields()) if init_data is None else init_data
        context = browser.new_context(viewport={"width": 390, "height": 844})
        extra = (DARK_THEME if dark else "") + (CLOUD_STUB if cloud else "")
        stub = SDK_STUB % (json.dumps(init_data), extra)
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


# ---------- История расчётов (CloudStorage) ----------


def calculate(page):
    press_main_button(page)
    expect(page.locator("#results")).to_be_visible(timeout=5000)


def history_items(page):
    return page.locator("#history-list .history-item")


def cloud_store(page) -> dict:
    return page.evaluate("JSON.parse(localStorage.getItem('__cloud_store') || '{}')")


def seed_cloud(page, store: dict):
    page.evaluate(f"localStorage.setItem('__cloud_store', {json.dumps(json.dumps(store))})")
    page.reload()


def test_history_hidden_without_cloud_storage(open_app):
    page = open_app()
    assert page.is_hidden("#history")
    fill_reference(page)
    calculate(page)
    assert page.is_hidden("#save-box")


def test_record_roundtrip_drops_empty_costs(open_app):
    page = open_app()
    restored = page.evaluate("""() => {
        const draft = readDraft(document.getElementById('calc'));
        return expandDraft(compactDraft(draft));
    }""")
    assert len(restored["tiers"]) == 1
    assert restored["costs"] == []  # все статьи по умолчанию пустые
    assert restored["scenarios"][1] == {"sell_through": "80", "probability": ""}


def test_save_list_and_delete(open_app):
    page = open_app(cloud=True)
    expect(page.locator("#history")).to_be_visible()
    fill_reference(page)
    calculate(page)

    page.fill("#save-name", "Арена, 12 марта")
    page.click("#save-calc")
    expect(page.locator("#save-msg")).to_have_text("Сохранено ✓")
    assert page.is_disabled("#save-calc")  # повторный тап не создаст дубль

    page.click("#history summary")
    expect(page.locator("#history-msg")).to_have_text("")  # не «Пока пусто» при непустом списке
    item = history_items(page).first
    expect(item).to_contain_text("Арена, 12 марта")
    expect(item).to_contain_text(f"−274{NBSP}800{NBSP}₽")
    expect(item).to_contain_text("окупается с 864 билетов")

    page.reload()  # запись живёт в облаке, а не в памяти страницы
    page.click("#history summary")
    expect(history_items(page)).to_have_count(1)

    history_items(page).first.locator(".delete-calc").click()
    expect(history_items(page)).to_have_count(0)
    assert not [k for k in cloud_store(page) if k.startswith("calc_")]


def test_open_record_recalculates_and_can_be_undone(open_app):
    page = open_app(cloud=True)
    fill_reference(page)
    calculate(page)
    page.click("#save-calc")
    expect(page.locator("#save-msg")).to_have_text("Сохранено ✓")

    # Пользователь начал новый концерт и не сохранил его
    page.fill('[data-path="capacity"]', "777")
    page.click("#history summary")
    history_items(page).first.locator(".open-calc").click()

    expect(page.locator("#undo-bar")).to_be_visible()
    assert page.input_value('[data-path="capacity"]') == "1000"
    expect(page.locator("#results")).to_be_visible(timeout=5000)
    expect(page.locator("#break-even")).to_contain_text("864 билета")
    # Страница уехала к результатам, а «Вернуть мою форму» всё равно на экране
    page.wait_for_timeout(600)  # плавная прокрутка
    assert page.evaluate("""() => {
        const r = document.getElementById('undo-btn').getBoundingClientRect();
        return r.top >= 0 && r.bottom <= window.innerHeight && window.scrollY > 0;
    }""")
    page.click("#undo-close")
    expect(page.locator("#undo-bar")).to_be_hidden()

    page.reload()  # открытая запись стала черновиком
    assert page.input_value('[data-path="capacity"]') == "1000"


def save_calculation(page, name: str):
    calculate(page)
    page.fill("#save-name", name)
    page.click("#save-calc")
    expect(page.locator("#save-msg")).to_have_text("Сохранено ✓")


def test_undo_after_opening_two_records_returns_original_form(open_app):
    page = open_app(cloud=True)
    fill_reference(page)
    save_calculation(page, "R1")  # вместимость 1000
    page.fill('[data-path="capacity"]', "1200")
    save_calculation(page, "R2")

    page.fill('[data-path="capacity"]', "777")  # несохранённая форма A
    page.click("#history summary")
    history_items(page).nth(0).locator(".open-calc").click()  # R2
    expect(page.locator("#undo-text")).to_contain_text("R2")
    history_items(page).nth(1).locator(".open-calc").click()  # R1
    expect(page.locator("#undo-text")).to_contain_text("R1")
    page.click("#undo-btn")
    assert page.input_value('[data-path="capacity"]') == "777"


HOLD_FETCH = """() => {
    const realFetch = window.fetch;
    window.__held = [];
    window.fetch = async (...args) => {
        const response = await realFetch(...args);
        await new Promise((resolve) => window.__held.push(resolve));
        return response;
    };
}"""


def release_next_response(page):
    page.wait_for_function("window.__held.length > 0")
    page.evaluate("window.__held.shift()()")


def test_open_during_request_recalculates_the_opened_record(open_app):
    page = open_app(cloud=True)
    fill_reference(page)
    save_calculation(page, "Запись")  # вместимость 1000, безубыточность 864 билета

    page.fill('[data-path="capacity"]', "1200")
    page.fill('[data-path="ticketing_fee_rate"]', "20")  # другая форма — другой результат
    page.evaluate(HOLD_FETCH)
    press_main_button(page)  # расчёт формы 1200 завис в пути
    page.wait_for_function("window.__held.length > 0")

    page.click("#history summary")
    history_items(page).first.locator(".open-calc").click()
    release_next_response(page)  # ответ по старой форме — должен считаться устаревшим
    # Промежуток до пересчёта: старый ответ показан, но сохранить его нельзя
    expect(page.locator("#results")).to_have_class("stale")
    expect(page.locator("#save-box")).to_be_hidden()
    release_next_response(page)  # пересчёт открытой записи

    expect(page.locator("#results")).not_to_have_class("stale")
    expect(page.locator("#break-even")).to_contain_text("864 билета")
    expect(page.locator("#save-box")).to_be_visible()
    # В историю уйдёт именно запись, а не форма 1200
    page.fill("#save-name", "Копия")
    page.click("#save-calc")
    expect(page.locator("#save-msg")).to_have_text("Сохранено ✓")
    saved = [json.loads(v) for v in cloud_store(page).values() if '"Копия"' in v]
    assert saved[0]["d"]["c"] == "1000"
    assert saved[0]["d"]["f"] == "10"


def test_zero_break_even_text_in_history(open_app):
    page = open_app(cloud=True)
    record = {
        "v": 1, "name": "Без расходов", "savedAt": 1_800_000_000_000,
        "d": {"c": "100", "k": "", "t": [["A", "100", "100"]], "x": [], "pt": "", "f": "",
              "m": "income", "r": "6", "vat": "0", "s": [["50", ""], ["80", ""], ["100", ""]]},
        "s": {"net": "1000.00", "be": 0},
    }
    seed_cloud(page, {"calc_zero": json.dumps(record)})
    page.click("#history summary")
    expect(history_items(page).first).to_contain_text("окупается с первого билета")


def test_undo_restores_unsaved_form(open_app):
    page = open_app(cloud=True)
    fill_reference(page)
    calculate(page)
    page.click("#save-calc")
    expect(page.locator("#save-msg")).to_have_text("Сохранено ✓")

    page.fill('[data-path="capacity"]', "777")
    page.click("#history summary")
    history_items(page).first.locator(".open-calc").click()
    expect(page.locator("#undo-bar")).to_be_visible()
    page.click("#undo-btn")
    assert page.input_value('[data-path="capacity"]') == "777"
    page.reload()
    assert page.input_value('[data-path="capacity"]') == "777"


def test_broken_record_is_shown_as_damaged_and_deletable(open_app):
    page = open_app(cloud=True)
    seed_cloud(page, {"calc_broken": "{oops", "calc_alien": json.dumps({"v": 99}), "other_key": "x"})
    page.click("#history summary")
    items = history_items(page)
    expect(items).to_have_count(2)  # чужие ключи без префикса не трогаем
    expect(items.first).to_contain_text("Повреждённая запись")
    assert items.first.locator(".open-calc").is_hidden()
    items.first.locator(".delete-calc").click()
    expect(items).to_have_count(1)
    assert "other_key" in cloud_store(page)


def test_history_limit(open_app):
    page = open_app(cloud=True)
    seed_cloud(page, {f"calc_{i:03d}": "{oops" for i in range(50)})
    fill_reference(page)
    calculate(page)
    page.click("#save-calc")
    expect(page.locator("#save-msg")).to_contain_text("История заполнена (50)")
    assert len(cloud_store(page)) == 50


def test_too_big_record_is_refused(open_app):
    page = open_app(cloud=True)
    fill_reference(page)
    for _ in range(45):
        page.click("#add-cost")
    rows = page.locator("#costs .row")
    for i in range(rows.count()):
        rows.nth(i).locator('[data-field="name"]').fill("Ж" * 64)
        rows.nth(i).locator('[data-field="amount"]').fill("1")
    calculate(page)
    page.click("#save-calc")
    expect(page.locator("#save-msg")).to_contain_text("слишком большой")
    assert not cloud_store(page)


def test_editing_after_calculation_hides_save(open_app):
    page = open_app(cloud=True)
    fill_reference(page)
    calculate(page)
    expect(page.locator("#save-box")).to_be_visible()
    page.fill('[data-path="per_ticket_cost"]', "60")
    expect(page.locator("#save-box")).to_be_hidden()


def test_edit_during_request_marks_result_stale(open_app):
    page = open_app(cloud=True)
    fill_reference(page)
    # Задерживаем ответ сервера, пока тест не «отпустит» его
    page.evaluate("""() => {
        const realFetch = window.fetch;
        window.fetch = async (...args) => {
            const response = await realFetch(...args);
            await new Promise((resolve) => { window.__release = resolve; });
            return response;
        };
    }""")
    press_main_button(page)
    page.wait_for_function("typeof window.__release === 'function'")
    page.fill('[data-path="per_ticket_cost"]', "60")  # правка, пока запрос в пути
    page.evaluate("window.__release()")

    expect(page.locator("#results")).to_be_visible(timeout=5000)
    expect(page.locator("#results")).to_have_class("stale")
    assert page.is_hidden("#save-box")  # устаревшие цифры в историю не сохранить
