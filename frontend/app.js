"use strict";

/*
 * Mini App калькулятора P&L концерта.
 * Деньги и ставки — только строками: никаких Number() для сумм, ни на вводе, ни на выводе.
 * Пользовательские строки — только через textContent.
 */

// ---------- Числа строками (чистые функции, без DOM) ----------

const PERCENT_UNITS = 1_000_000; // 100% = 1 000 000 единиц по 0.0001%

function normalizeNumber(raw) {
  return String(raw ?? "").replace(/[\s ]/g, "").replace(",", ".");
}

/** "1 500,5" -> "1500.5"; null, если не деньги (до 999 999 999 999.99, 2 знака). */
function parseMoney(raw) {
  const value = normalizeNumber(raw);
  return /^\d{1,12}(\.\d{1,2})?$/.test(value) ? value : null;
}

/** Целое 0..1 000 000 или null. */
function parseCount(raw) {
  const value = normalizeNumber(raw);
  if (!/^\d{1,7}$/.test(value)) return null;
  const count = Number(value); // целые до 10^7 Number хранит точно
  return count <= 1_000_000 ? count : null;
}

/** Проценты -> {units, share}: "10,5" -> {units: 105000, share: "0.105"}; null, если не число. */
function parsePercent(raw) {
  const match = /^(\d{1,3})(?:\.(\d{1,4}))?$/.exec(normalizeNumber(raw));
  if (!match) return null;
  const units = Number(match[1]) * 10_000 + Number((match[2] ?? "").padEnd(4, "0"));
  return { units, share: unitsToShare(units) };
}

function unitsToShare(units) {
  const whole = Math.floor(units / PERCENT_UNITS);
  const frac = String(units % PERCENT_UNITS).padStart(6, "0").replace(/0+$/, "");
  return frac ? `${whole}.${frac}` : String(whole);
}

function groupDigits(digits) {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}

function splitDecimal(value) {
  const negative = value.startsWith("-");
  const [whole, frac = ""] = value.replace(/^[-+]/, "").split(".");
  return { negative, whole, frac };
}

/** "-274800.00" -> "−274 800 ₽" (копейки показываем, только если они есть). */
function formatMoney(value) {
  const { negative, whole, frac } = splitDecimal(value);
  const kopecks = frac.replace(/0+$/, "") ? `,${frac.padEnd(2, "0")}` : "";
  const isZero = /^0*$/.test(whole) && !kopecks;
  return `${negative && !isZero ? "−" : ""}${groupDigits(whole)}${kopecks} ₽`;
}

/** Доля строкой -> проценты: "0.9600" -> "96 %", "-1.3513" -> "−135,13 %". */
function formatShare(value) {
  const { negative, whole, frac } = splitDecimal(value);
  const padded = frac.padEnd(2, "0");
  const percentWhole = (whole + padded.slice(0, 2)).replace(/^0+(?=\d)/, "");
  const percentFrac = padded.slice(2).replace(/0+$/, "");
  const isZero = percentWhole === "0" && !percentFrac;
  const sign = negative && !isZero ? "−" : "";
  return `${sign}${groupDigits(percentWhole)}${percentFrac ? "," + percentFrac : ""} %`;
}

function formatUnits(units) {
  return formatShare(unitsToShare(units));
}

// ---------- История: формат записи (чистые функции, без DOM) ----------

const HISTORY_PREFIX = "calc_";
const HISTORY_LIMIT = 50;
const CLOUD_MAX_BYTES = 4096; // лимит значения CloudStorage; считаем в байтах UTF-8 — с запасом
const CLOUD_TIMEOUT_MS = 10_000;
const RECORD_VERSION = 1;

const isStr = (v) => typeof v === "string";
const isRowOf = (n) => (row) => Array.isArray(row) && row.length === n && row.every(isStr);

/** Черновик формы -> компактный вид: строки массивами, пустые статьи расходов выкидываем. */
function compactDraft(draft) {
  return {
    c: draft.capacity,
    k: draft.comps,
    t: draft.tiers.map((t) => [t.name, t.price, t.quantity]),
    x: draft.costs.filter((c) => c.amount.trim() !== "").map((c) => [c.name, c.amount]),
    pt: draft.per_ticket_cost,
    f: draft.ticketing_fee_rate,
    m: draft.tax_mode,
    r: draft.tax_rate,
    vat: draft.vat_rate,
    s: draft.scenarios.map((s) => [s.sell_through, s.probability]),
  };
}

/** Обратно в формат readDraft; null, если структура чужая или битая. */
function expandDraft(d) {
  if (!d || typeof d !== "object") return null;
  const scalars = [d.c, d.k, d.pt, d.f, d.m, d.r, d.vat];
  if (!scalars.every(isStr)) return null;
  if (![d.t, d.x, d.s].every(Array.isArray)) return null;
  if (!d.t.every(isRowOf(3)) || !d.x.every(isRowOf(2)) || !d.s.every(isRowOf(2))) return null;
  return {
    capacity: d.c,
    comps: d.k,
    tiers: d.t.map(([name, price, quantity]) => ({ name, price, quantity })),
    costs: d.x.map(([name, amount]) => ({ name, amount })),
    per_ticket_cost: d.pt,
    ticketing_fee_rate: d.f,
    tax_mode: d.m,
    tax_rate: d.r,
    vat_rate: d.vat,
    scenarios: d.s.map(([sell_through, probability]) => ({ sell_through, probability })),
  };
}

/** summary: {net: прибыль базового сценария строкой, be: билеты безубыточности или null}. */
function packRecord(name, savedAt, draft, summary) {
  return JSON.stringify({ v: RECORD_VERSION, name, savedAt, d: compactDraft(draft), s: summary });
}

function unpackRecord(raw) {
  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!data || data.v !== RECORD_VERSION || !isStr(data.name) || !Number.isFinite(data.savedAt)) return null;
  const draft = expandDraft(data.d);
  const summary = data.s;
  if (!draft || !summary || !isStr(summary.net)) return null;
  if (summary.be !== null && !Number.isInteger(summary.be)) return null;
  return { name: data.name, savedAt: data.savedAt, draft, summary };
}

function byteLength(text) {
  return new TextEncoder().encode(text).length;
}

function newHistoryKey() {
  // Время + случайный хвост: два устройства в одну миллисекунду не перезапишут друг друга
  const random = Math.floor(Math.random() * 36 ** 4).toString(36).padStart(4, "0");
  return `${HISTORY_PREFIX}${Date.now().toString(36)}${random}`;
}

// ---------- CloudStorage Telegram ----------

class CloudTimeout extends Error {}

function cloudStorageOf(tg) {
  try {
    return tg?.isVersionAtLeast?.("6.9") && tg.CloudStorage ? tg.CloudStorage : null;
  } catch {
    return null;
  }
}

/** Callback -> Promise. Без моста к Telegram callback не вызывается никогда — отсюда таймаут. */
function cloudCall(storage, method, ...args) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new CloudTimeout(method)), CLOUD_TIMEOUT_MS);
    try {
      storage[method](...args, (error, result) => {
        clearTimeout(timer);
        if (error) reject(new Error(String(error)));
        else resolve(result);
      });
    } catch (error) {
      clearTimeout(timer); // старая версия Telegram бросает синхронно
      reject(error);
    }
  });
}

// ---------- Конфигурация формы ----------

const DRAFT_KEY = "concert-pnl:form:v1";
const REQUEST_TIMEOUT_MS = 15_000;
const LIMITS = { tiers: 20, costs: 50 };

const SCENARIOS = [
  { name: "Пессимистичный", sell_through: "50" },
  { name: "Базовый", sell_through: "80" },
  { name: "Оптимистичный", sell_through: "100" },
];

const TAX_MODES = {
  income: { rate: "6", hint: "Налог с выручки без НДС — платится и при убытке." },
  income_minus_expenses: {
    rate: "15",
    hint: "Налог с прибыли, но не меньше 1% выручки (минимальный налог).",
  },
  profit: { rate: "25", hint: "Налог только с положительной прибыли." },
};

const DEFAULT_DRAFT = {
  capacity: "",
  comps: "",
  tiers: [{ name: "Стандарт", price: "", quantity: "" }],
  costs: [
    { name: "Гонорар артиста", amount: "" },
    { name: "Аренда площадки", amount: "" },
    { name: "Продакшн", amount: "" },
    { name: "Маркетинг", amount: "" },
  ],
  per_ticket_cost: "",
  ticketing_fee_rate: "",
  tax_mode: "income",
  tax_rate: TAX_MODES.income.rate,
  vat_rate: "0",
  scenarios: SCENARIOS.map((s) => ({ sell_through: s.sell_through, probability: "" })),
};

// ---------- Сбор и проверка формы ----------

function fieldValue(root, field) {
  return root.querySelector(`[data-field="${field}"]`).value;
}

function rowsOf(listId) {
  return [...document.getElementById(listId).children];
}

/**
 * Возвращает payload для API, ошибки [{path, msg}] и соответствие индексов
 * payload -> строк формы (пустые статьи расходов не отправляются).
 */
function collectForm(form) {
  const errors = [];
  const fail = (path, msg) => errors.push({ path, msg });

  const capacity = parseCount(fieldValue(form, "capacity"));
  if (capacity === null || capacity === 0) fail("capacity", "Целое число мест больше нуля");

  const compsRaw = fieldValue(form, "comps").trim();
  const comps = compsRaw === "" ? 0 : parseCount(compsRaw);
  if (comps === null) fail("comps", "Целое число");

  const tiers = [];
  rowsOf("tiers").forEach((row, i) => {
    const name = fieldValue(row, "name").trim();
    const price = parseMoney(fieldValue(row, "price"));
    const quantity = parseCount(fieldValue(row, "quantity"));
    if (!name) fail(`tiers.${i}.name`, "Название категории");
    if (price === null) fail(`tiers.${i}.price`, "Цена в рублях, до 2 знаков после запятой");
    if (quantity === null) fail(`tiers.${i}.quantity`, "Целое число мест");
    tiers.push({ name, price, quantity });
  });
  if (tiers.length === 0) fail(null, "Добавь хотя бы одну категорию билетов");

  if (capacity && comps !== null && tiers.every((t) => t.quantity !== null)) {
    const allocated = tiers.reduce((sum, t) => sum + t.quantity, 0);
    if (allocated + comps > capacity) {
      fail("capacity", `Билетов и пригласительных ${allocated + comps} — больше вместимости`);
    }
  }

  const fixedCosts = [];
  const costRows = [];
  rowsOf("costs").forEach((row, i) => {
    const name = fieldValue(row, "name").trim();
    const amountRaw = fieldValue(row, "amount").trim();
    if (amountRaw === "") return; // незаполненная статья — просто не учитываем
    const amount = parseMoney(amountRaw);
    if (!name) fail(`fixed_costs.${i}.name`, "Название статьи");
    if (amount === null) fail(`fixed_costs.${i}.amount`, "Сумма в рублях, до 2 знаков после запятой");
    fixedCosts.push({ name, amount });
    costRows.push(i);
  });

  const perTicketRaw = fieldValue(form, "per_ticket_cost").trim();
  const perTicketCost = perTicketRaw === "" ? "0" : parseMoney(perTicketRaw);
  if (perTicketCost === null) fail("per_ticket_cost", "Сумма в рублях");

  const feeRaw = fieldValue(form, "ticketing_fee_rate").trim();
  const fee = feeRaw === "" ? parsePercent("0") : parsePercent(feeRaw);
  if (fee === null || fee.units >= PERCENT_UNITS) {
    fail("ticketing_fee_rate", "Процент от 0 до 100 (не включая), до 4 знаков");
  }

  const taxRate = parsePercent(fieldValue(form, "tax_rate"));
  if (taxRate === null || taxRate.units >= PERCENT_UNITS) {
    fail("tax.rate", "Процент от 0 до 100 (не включая)");
  }

  const scenarios = [];
  let probabilitySum = 0;
  let probabilityCount = 0;
  rowsOf("scenarios").forEach((row, i) => {
    const sell = parsePercent(fieldValue(row, "sell_through"));
    if (sell === null || sell.units > PERCENT_UNITS) {
      fail(`scenarios.${i}.sell_through`, "Процент от 0 до 100");
    }
    const probabilityRaw = fieldValue(row, "probability").trim();
    let probability = null;
    if (probabilityRaw !== "") {
      probability = parsePercent(probabilityRaw);
      if (probability === null || probability.units > PERCENT_UNITS) {
        fail(`scenarios.${i}.probability`, "Процент от 0 до 100");
      } else {
        probabilitySum += probability.units;
        probabilityCount += 1;
      }
    }
    scenarios.push({
      name: SCENARIOS[i].name,
      sell_through: sell?.share ?? null,
      ...(probability ? { probability: probability.share } : {}),
    });
  });
  if (probabilityCount > 0 && probabilityCount < scenarios.length) {
    fail(null, "Вероятности нужно заполнить у всех сценариев или ни у одного");
  } else if (probabilityCount > 0 && probabilitySum !== PERCENT_UNITS) {
    fail(null, `Сумма вероятностей ${formatUnits(probabilitySum)}, а должна быть 100 %`);
  }

  const payload = {
    capacity,
    comps,
    tiers,
    fixed_costs: fixedCosts,
    per_ticket_cost: perTicketCost,
    ticketing_fee_rate: fee?.share ?? null,
    tax: {
      mode: fieldValue(form, "tax_mode"),
      rate: taxRate?.share ?? null,
      vat_rate: fieldValue(form, "vat_rate"),
    },
    scenarios,
  };
  return { payload, errors, rowIndex: { fixed_costs: costRows } };
}

// ---------- Ошибки ----------

function clearErrors() {
  document.querySelectorAll(".invalid").forEach((el) => el.classList.remove("invalid"));
  document.querySelectorAll(".field-error").forEach((el) => el.remove());
  const list = document.getElementById("form-errors");
  list.replaceChildren();
  list.hidden = true;
}

function fieldByPath(path) {
  if (!path) return null;
  const [section, index, field] = path.split(".");
  // Поля внутри строк списков адресуем по позиции строки
  const listIds = { tiers: "tiers", fixed_costs: "costs", scenarios: "scenarios" };
  if (listIds[section] && index !== undefined) {
    const row = document.getElementById(listIds[section]).children[Number(index)];
    return row?.querySelector(`[data-field="${field}"]`) ?? null;
  }
  return document.querySelector(`[data-path="${path}"]`);
}

function renderErrors(errors) {
  clearErrors();
  const general = document.getElementById("form-errors");
  let first = null;
  for (const { path, msg } of errors) {
    const input = fieldByPath(path);
    if (input) {
      input.classList.add("invalid");
      const note = document.createElement("div");
      note.className = "field-error";
      note.textContent = msg;
      input.insertAdjacentElement("afterend", note);
      first ??= input;
    } else {
      const item = document.createElement("li");
      item.textContent = msg;
      general.append(item);
    }
  }
  general.hidden = general.childElementCount === 0;
  (first ?? (general.hidden ? null : general))?.scrollIntoView({ behavior: "smooth", block: "center" });
}

/** 422 от API: строка из core или массив ошибок pydantic с loc. */
function errorsFrom422(detail, rowIndex) {
  if (!Array.isArray(detail)) return [{ path: null, msg: String(detail) }];
  return detail.map((item) => {
    const loc = (item.loc ?? []).slice(1); // без "body"
    if (loc[0] === "fixed_costs" && typeof loc[1] === "number") {
      loc[1] = rowIndex.fixed_costs[loc[1]] ?? loc[1];
    }
    return { path: loc.length ? loc.join(".") : null, msg: item.msg ?? "Неверное значение" };
  });
}

// ---------- API ----------

class ApiError extends Error {
  constructor(errors) {
    super(errors.map((e) => e.msg).join("; "));
    this.errors = errors;
  }
}

async function requestReport(payload, initData, rowIndex) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let response;
  try {
    response = await fetch("api/calculate", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Telegram-Init-Data": initData },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
  } catch {
    throw new ApiError([{ path: null, msg: "Нет связи с сервером. Проверь интернет и попробуй ещё раз." }]);
  } finally {
    clearTimeout(timer);
  }

  if (response.status === 401) {
    throw new ApiError([
      { path: null, msg: "Сессия устарела. Закрой калькулятор и открой его заново через бота." },
    ]);
  }
  const body = await response.json().catch(() => null);
  if (response.status === 422 && body) throw new ApiError(errorsFrom422(body.detail, rowIndex));
  if (!response.ok || !body) {
    throw new ApiError([{ path: null, msg: `Сервер недоступен (код ${response.status}). Попробуй позже.` }]);
  }
  return body;
}

// ---------- Результаты ----------

function setText(root, selector, text) {
  root.querySelector(selector).textContent = text;
}

/** Отрицательное только с ненулевой цифрой: "-0.00" — это ноль. */
function isNegative(value) {
  return value.startsWith("-") && /[1-9]/.test(value);
}

function signClass(value) {
  return isNegative(value) ? "negative" : /[1-9]/.test(value) ? "positive" : "";
}

/** plural(21, ["билет", "билета", "билетов"]) -> "билет" */
function plural(n, [one, few, many]) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

function renderBreakEven(breakEven) {
  const box = document.getElementById("break-even");
  const title = document.createElement("h2");
  title.textContent = "Точка безубыточности";
  const big = document.createElement("p");
  big.className = "big";
  const note = document.createElement("p");
  note.className = "hint";

  if (breakEven === null) {
    big.textContent = "Не окупается";
    big.classList.add("negative");
    note.textContent = "Даже при полностью проданных билетах — убыток.";
  } else if (breakEven.tickets === 0) {
    big.textContent = "С первого билета";
    big.classList.add("positive");
    note.textContent = "Постоянных расходов нет, каждый билет приносит прибыль.";
  } else {
    const tickets = breakEven.tickets;
    const word = plural(tickets, ["билет", "билета", "билетов"]);
    big.textContent = `${groupDigits(String(tickets))} ${word} · ${formatShare(breakEven.sell_through)}`;
    note.textContent = `Выручка в этой точке — ${formatMoney(breakEven.gross_revenue)}. Доля — от билетов в продаже.`;
  }
  box.replaceChildren(title, big, note);
}

function renderSummary(report) {
  const box = document.getElementById("summary");
  const list = document.createElement("dl");
  const add = (label, value) => {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = formatMoney(value);
    dd.className = signClass(value);
    list.append(dt, dd);
  };
  if (report.expected_net_profit !== null) add("Ожидаемая прибыль", report.expected_net_profit);
  add("Если не продано ни одного билета", report.zero_sales_net_profit);
  const title = document.createElement("h2");
  title.textContent = "Риски";
  box.replaceChildren(title, list);
}

function renderScenario(result) {
  const card = document.getElementById("scenario-card-tpl").content.firstElementChild.cloneNode(true);
  setText(card, ".name", `${result.name} · ${formatShare(result.sell_through)}`);
  const net = card.querySelector(".net-profit");
  net.textContent = formatMoney(result.net_profit);
  net.classList.add(signClass(result.net_profit) || "positive");
  setText(card, ".tickets", groupDigits(String(result.tickets_sold)));
  setText(card, ".gross", formatMoney(result.gross_revenue));
  setText(card, ".expenses", formatMoney(result.total_expenses));
  setText(card, ".tax", formatMoney(result.tax));
  setText(card, ".margin", result.margin === null ? "—" : formatShare(result.margin));

  const safety = card.querySelector(".safety");
  if (result.margin_of_safety === null) {
    safety.textContent = "—";
  } else {
    safety.textContent = formatShare(result.margin_of_safety);
    safety.className = `safety ${signClass(result.margin_of_safety)}`;
  }
  if (isNegative(result.net_profit)) {
    const warning = card.querySelector(".warning");
    warning.hidden = false;
    warning.textContent = "Убыток: продаж не хватает до точки безубыточности.";
  }
  return card;
}

function renderReport(report) {
  renderBreakEven(report.break_even);
  renderSummary(report);
  document.getElementById("scenario-cards").replaceChildren(...report.scenarios.map(renderScenario));
  const results = document.getElementById("results");
  results.hidden = false;
  results.classList.remove("stale");
  results.scrollIntoView({ behavior: "smooth", block: "start" });
}

// Последний актуальный расчёт: ровно те цифры, по которым он сделан. null — сохранять нечего
let lastCalc = null;

function hideSaveBox() {
  lastCalc = null;
  document.getElementById("save-box").hidden = true;
}

function markResultsStale() {
  document.getElementById("results").classList.add("stale");
  hideSaveBox();
}

function hideResults() {
  document.getElementById("results").hidden = true;
  hideSaveBox();
}

// ---------- Строки списков ----------

function addRow(listId, templateId, values = {}) {
  const row = document.getElementById(templateId).content.firstElementChild.cloneNode(true);
  const entries = values && typeof values === "object" ? Object.entries(values) : [];
  for (const [field, value] of entries) {
    const input = row.querySelector(`[data-field="${field}"]`);
    if (input && (typeof value === "string" || typeof value === "number")) input.value = value;
  }
  document.getElementById(listId).append(row);
  return row;
}

function updateAddButtons() {
  document.getElementById("add-tier").disabled = rowsOf("tiers").length >= LIMITS.tiers;
  document.getElementById("add-cost").disabled = rowsOf("costs").length >= LIMITS.costs;
}

// ---------- Подсказки при вводе ----------

function updateSeatsHint(form) {
  const hint = document.getElementById("seats-hint");
  const capacity = parseCount(fieldValue(form, "capacity"));
  const compsRaw = fieldValue(form, "comps").trim();
  const comps = compsRaw === "" ? 0 : parseCount(compsRaw);
  const quantities = rowsOf("tiers").map((row) => parseCount(fieldValue(row, "quantity")) ?? 0);
  if (!capacity || comps === null) {
    hint.textContent = "";
    return;
  }
  const free = capacity - comps - quantities.reduce((a, b) => a + b, 0);
  hint.classList.toggle("bad", free < 0);
  hint.textContent =
    free >= 0
      ? `Не распределено по категориям: ${groupDigits(String(free))} мест`
      : `Перебор: категорий и пригласительных больше вместимости на ${groupDigits(String(-free))}`;
}

function updateProbabilityHint() {
  const hint = document.getElementById("prob-hint");
  const values = rowsOf("scenarios").map((row) => fieldValue(row, "probability").trim());
  const filled = values.filter((v) => v !== "");
  if (filled.length === 0) {
    hint.textContent = "";
    return;
  }
  const parsed = filled.map(parsePercent);
  if (parsed.includes(null)) {
    hint.textContent = "";
    return;
  }
  const sum = parsed.reduce((acc, p) => acc + p.units, 0);
  hint.classList.toggle("bad", sum !== PERCENT_UNITS || filled.length !== values.length);
  hint.textContent = `Сумма вероятностей: ${formatUnits(sum)}`;
}

function updateTaxHint(form) {
  document.getElementById("tax-hint").textContent = TAX_MODES[fieldValue(form, "tax_mode")].hint;
}

// ---------- Черновик ----------

function readDraft(form) {
  const rows = (listId, fields) =>
    rowsOf(listId).map((row) => Object.fromEntries(fields.map((f) => [f, fieldValue(row, f)])));
  return {
    capacity: fieldValue(form, "capacity"),
    comps: fieldValue(form, "comps"),
    tiers: rows("tiers", ["name", "price", "quantity"]),
    costs: rows("costs", ["name", "amount"]),
    per_ticket_cost: fieldValue(form, "per_ticket_cost"),
    ticketing_fee_rate: fieldValue(form, "ticketing_fee_rate"),
    tax_mode: fieldValue(form, "tax_mode"),
    tax_rate: fieldValue(form, "tax_rate"),
    vat_rate: fieldValue(form, "vat_rate"),
    scenarios: rows("scenarios", ["sell_through", "probability"]),
  };
}

function loadDraft() {
  try {
    const draft = JSON.parse(localStorage.getItem(DRAFT_KEY));
    if (draft && Array.isArray(draft.tiers) && Array.isArray(draft.costs)) return draft;
  } catch {
    // нет доступа к localStorage или битый JSON — начинаем с чистой формы
  }
  return DEFAULT_DRAFT;
}

function saveDraft(form) {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify(readDraft(form)));
  } catch {
    // переполнение или запрет хранилища — черновик не критичен
  }
}

function applyDraft(form, draft) {
  for (const field of ["capacity", "comps", "per_ticket_cost", "ticketing_fee_rate", "tax_rate"]) {
    const value = draft[field];
    form.querySelector(`[data-field="${field}"]`).value = typeof value === "string" ? value : "";
  }
  const selectValue = (field, value, fallback) => {
    const select = form.querySelector(`[data-field="${field}"]`);
    select.value = value;
    if (select.value !== value) select.value = fallback; // значение из старого черновика
  };
  selectValue("tax_mode", draft.tax_mode, "income");
  selectValue("vat_rate", draft.vat_rate, "0");

  document.getElementById("tiers").replaceChildren();
  draft.tiers.slice(0, LIMITS.tiers).forEach((t) => addRow("tiers", "tier-tpl", t));
  document.getElementById("costs").replaceChildren();
  draft.costs.slice(0, LIMITS.costs).forEach((c) => addRow("costs", "cost-tpl", c));

  document.getElementById("scenarios").replaceChildren();
  SCENARIOS.forEach((scenario, i) => {
    const row = addRow("scenarios", "scenario-tpl", draft.scenarios?.[i] ?? scenario);
    row.querySelector(".scenario-name").textContent = scenario.name;
  });
}

function refreshFormState(form) {
  updateAddButtons();
  updateSeatsHint(form);
  updateProbabilityHint();
  updateTaxHint(form);
  clearErrors();
}

// ---------- История расчётов ----------

function defaultCalcName() {
  return `Концерт ${new Date().toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" })}`;
}

function summaryOf(report) {
  const base = report.scenarios.find((s) => s.name === SCENARIOS[1].name) ?? report.scenarios[0];
  return { net: base.net_profit, be: report.break_even?.tickets ?? null };
}

/**
 * Раздел «История»: список из CloudStorage, сохранение, открытие с возможностью вернуть форму.
 * edited() — пометить идущий расчёт устаревшим; recalculate() — посчитать заново (в том числе
 * после текущего запроса). Возвращает onCalculated(snapshot, report) для свежего расчёта.
 */
function setupHistory(form, storage, { edited, recalculate }) {
  const section = document.getElementById("history");
  const list = document.getElementById("history-list");
  const count = document.getElementById("history-count");
  const message = document.getElementById("history-msg");
  const undoBar = document.getElementById("undo-bar");
  const saveBox = document.getElementById("save-box");
  const saveName = document.getElementById("save-name");
  const saveButton = document.getElementById("save-calc");
  const saveMessage = document.getElementById("save-msg");

  let items = []; // [{key, record | null}] — null у повреждённых записей, они тоже занимают место
  let previousDraft = null;

  section.hidden = false;
  const say = (el, text, bad = false) => {
    el.textContent = text;
    el.classList.toggle("bad", bad);
  };

  function render() {
    count.textContent = items.length ? `(${items.length})` : "";
    list.replaceChildren(
      ...items.map(({ key, record }) => {
        const item = document.getElementById("history-item-tpl").content.firstElementChild.cloneNode(true);
        item.dataset.key = key;
        if (!record) {
          setText(item, ".name", "Повреждённая запись");
          item.querySelector(".open-calc").hidden = true;
          return item;
        }
        setText(item, ".name", record.name);
        setText(item, ".date", new Date(record.savedAt).toLocaleDateString("ru-RU"));
        const profit = item.querySelector(".profit");
        profit.textContent = formatMoney(record.summary.net);
        profit.className = `profit ${signClass(record.summary.net)}`;
        const be = record.summary.be;
        let beText = `окупается с ${groupDigits(String(be))} ${plural(be, ["билета", "билетов", "билетов"])}`;
        if (be === null) beText = "не окупается";
        else if (be === 0) beText = "окупается с первого билета";
        setText(item, ".be", beText);
        return item;
      }),
    );
    say(message, items.length ? "" : "Пока пусто: посчитай концерт и нажми «Сохранить в историю».");
  }

  async function load() {
    say(message, "Загружаю…");
    const keys = (await cloudCall(storage, "getKeys")).filter((k) => k.startsWith(HISTORY_PREFIX));
    const values = {};
    for (let i = 0; i < keys.length; i += 25) {
      Object.assign(values, await cloudCall(storage, "getItems", keys.slice(i, i + 25)));
    }
    items = keys.map((key) => ({ key, record: isStr(values[key]) ? unpackRecord(values[key]) : null }));
    // Свежие сверху, повреждённые в конце
    items.sort((a, b) => (b.record?.savedAt ?? -1) - (a.record?.savedAt ?? -1));
    say(message, "");
    render();
  }

  function hideUndo() {
    undoBar.hidden = true;
    document.body.classList.remove("has-undo");
    previousDraft = null;
  }

  function showForm(draft) {
    applyDraft(form, draft);
    // Программная подстановка не порождает input: черновик сохраняем сами, а идущий
    // расчёт помечаем устаревшим — иначе ответ по старой форме покажется под новой
    edited();
    saveDraft(form);
    refreshFormState(form);
    hideResults();
  }

  function open(record) {
    const current = readDraft(form);
    try {
      showForm(record.draft);
    } catch (error) {
      console.warn("Не удалось открыть запись", error);
      showForm(current);
      say(message, "Эту запись не получилось открыть.", true);
      return;
    }
    // «Моя форма» — та, что была до ПЕРВОГО открытия: R1, затем R2 не должны затереть её
    previousDraft ??= current;
    document.getElementById("undo-text").textContent = `Открыт «${record.name}».`;
    undoBar.hidden = false;
    document.body.classList.add("has-undo");
    form.scrollIntoView({ behavior: "smooth", block: "start" });
    recalculate();
  }

  async function remove(key, button) {
    button.disabled = true;
    try {
      await cloudCall(storage, "removeItem", key);
      items = items.filter((item) => item.key !== key);
      render();
    } catch (error) {
      button.disabled = false;
      say(message, "Не удалось удалить. Попробуй ещё раз.", true);
      console.warn(error);
    }
  }

  async function save() {
    const calc = lastCalc;
    if (!calc) return;
    saveButton.disabled = true;
    say(saveMessage, "Сохраняю…");
    // Лимит считаем по загруженной истории: без неё можно записать 51-ю запись
    try {
      await loaded;
    } catch {
      saveButton.disabled = false;
      say(saveMessage, "История недоступна. Открой калькулятор заново.", true);
      return;
    }
    // Пока ждали, форму могли поменять и пересчитать: интерфейс уже про другой расчёт
    const stillCurrent = () => lastCalc === calc;
    if (!stillCurrent()) return;
    if (items.length >= HISTORY_LIMIT) {
      saveButton.disabled = false;
      say(saveMessage, `История заполнена (${HISTORY_LIMIT}). Удали ненужные расчёты.`, true);
      return;
    }
    const name = saveName.value.trim() || defaultCalcName();
    const raw = packRecord(name, Date.now(), calc.draft, summaryOf(calc.report));
    if (byteLength(raw) > CLOUD_MAX_BYTES) {
      saveButton.disabled = false;
      say(saveMessage, "Расчёт слишком большой для истории: сократи названия или число статей.", true);
      return;
    }
    const key = newHistoryKey();
    try {
      if ((await cloudCall(storage, "setItem", key, raw)) !== true) throw new Error("not stored");
      items.unshift({ key, record: unpackRecord(raw) });
      render();
      if (!stillCurrent()) return;
      say(saveMessage, "Сохранено ✓");
      saveButton.textContent = "Сохранено";
      lastCalc = null; // повторный тап не создаст дубль
    } catch (error) {
      console.warn(error);
      if (!stillCurrent()) return;
      saveButton.disabled = false;
      say(
        saveMessage,
        error instanceof CloudTimeout
          ? "Не удалось подтвердить сохранение. Открой калькулятор заново и проверь историю."
          : "Не удалось сохранить. Попробуй ещё раз.",
        true,
      );
    }
  }

  list.addEventListener("click", (event) => {
    const item = event.target.closest(".history-item");
    if (!item) return;
    const entry = items.find((i) => i.key === item.dataset.key);
    if (!entry) return;
    if (event.target.closest(".open-calc") && entry.record) open(entry.record);
    const del = event.target.closest(".delete-calc");
    if (del) remove(entry.key, del);
  });

  document.getElementById("undo-btn").addEventListener("click", () => {
    if (previousDraft) showForm(previousDraft);
    hideUndo();
  });
  document.getElementById("undo-close").addEventListener("click", hideUndo);
  form.addEventListener("input", hideUndo);
  saveButton.addEventListener("click", save);

  const loaded = load();
  loaded.catch((error) => {
    say(message, "Историю загрузить не удалось. Открой калькулятор заново.", true);
    console.warn(error);
  });

  return function onCalculated(snapshot, report) {
    lastCalc = { draft: snapshot, report };
    saveName.value = defaultCalcName();
    saveButton.disabled = false;
    saveButton.textContent = "Сохранить в историю";
    say(saveMessage, "");
    saveBox.hidden = false;
  };
}

// ---------- Запуск ----------

function init() {
  const tg = window.Telegram?.WebApp;
  const initData = tg?.initData ?? "";
  const inTelegram = initData !== "";
  const form = document.getElementById("calc");
  const fallbackSubmit = document.getElementById("fallback-submit");

  try {
    applyDraft(form, loadDraft());
  } catch (error) {
    // Черновик неожиданной формы не должен класть приложение при каждом открытии
    console.warn("Черновик повреждён, сбрасываю", error);
    try {
      localStorage.removeItem(DRAFT_KEY);
    } catch {
      // хранилище недоступно — достаточно формы по умолчанию
    }
    applyDraft(form, DEFAULT_DRAFT);
  }
  refreshFormState(form);

  // Номер правки: если форму поменяли, пока шёл запрос, пришедший ответ уже устарел
  let editVersion = 0;
  const edited = () => {
    editVersion += 1;
    markResultsStale();
  };

  // Сохраняем сразу, без debounce: Mini App часто закрывают сразу после ввода, а форма весит ~2 КБ
  form.addEventListener("input", () => {
    updateSeatsHint(form);
    updateProbabilityHint();
    edited();
    saveDraft(form);
  });

  form.querySelector('[data-field="tax_mode"]').addEventListener("change", (event) => {
    form.querySelector('[data-field="tax_rate"]').value = TAX_MODES[event.target.value].rate;
    updateTaxHint(form);
    edited();
    saveDraft(form);
  });

  document.getElementById("add-tier").addEventListener("click", () => {
    addRow("tiers", "tier-tpl").querySelector("input").focus();
    updateAddButtons();
    edited();
    saveDraft(form);
  });
  document.getElementById("add-cost").addEventListener("click", () => {
    addRow("costs", "cost-tpl").querySelector("input").focus();
    updateAddButtons();
    edited();
    saveDraft(form);
  });
  form.addEventListener("click", (event) => {
    const button = event.target.closest(".remove");
    if (!button) return;
    button.closest(".row").remove();
    edited();
    updateAddButtons();
    updateSeatsHint(form);
    clearErrors(); // индексы строк сдвинулись — старые подсветки неактуальны
    saveDraft(form);
  });

  let onCalculated = null; // появится, если доступна история
  let inFlight = false;
  let pendingRecalc = false; // программный пересчёт, запрошенный во время идущего запроса
  const setBusy = (busy) => {
    fallbackSubmit.disabled = busy;
    if (!inTelegram) return;
    if (busy) tg.MainButton.showProgress(false);
    else tg.MainButton.hideProgress();
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (inFlight) return;
    const { payload, errors, rowIndex } = collectForm(form);
    if (errors.length) {
      hideResults();
      renderErrors(errors);
      return;
    }
    clearErrors();
    inFlight = true;
    setBusy(true);
    // Снимок до запроса: в историю уйдут ровно те цифры, по которым посчитано
    const snapshot = readDraft(form);
    const version = editVersion;
    try {
      const report = await requestReport(payload, initData, rowIndex);
      renderReport(report);
      if (version !== editVersion) markResultsStale();
      else onCalculated?.(snapshot, report);
    } catch (error) {
      hideResults(); // старые цифры рядом с ошибкой легко принять за текущие
      renderErrors(error instanceof ApiError ? error.errors : [{ path: null, msg: "Что-то пошло не так." }]);
      if (!(error instanceof ApiError)) console.error(error);
    } finally {
      inFlight = false;
      setBusy(false);
      if (pendingRecalc) {
        pendingRecalc = false;
        submitForm();
      }
    }
  });

  const submitForm = () => {
    // requestSubmit нет в старых WebView (iOS < 16)
    if (form.requestSubmit) form.requestSubmit();
    else form.dispatchEvent(new Event("submit", { cancelable: true }));
  };

  if (inTelegram) {
    tg.ready();
    tg.expand();
    fallbackSubmit.hidden = true;
    tg.MainButton.setText("Рассчитать");
    tg.MainButton.show();
    // Обработчик регистрируем один раз: повторные onClick копятся и шлют запрос несколько раз
    tg.MainButton.onClick(submitForm);
  } else {
    document.getElementById("outside-banner").hidden = false;
  }

  // История — только в Telegram с CloudStorage (Bot API 6.9+); грузится в фоне, не задерживая форму
  const storage = inTelegram ? cloudStorageOf(tg) : null;
  // Двойной тап по «Рассчитать» по-прежнему гасится inFlight; а открытие записи во время
  // запроса не должно потеряться — его пересчитаем сразу после текущего
  const recalculate = () => {
    if (inFlight) pendingRecalc = true;
    else submitForm();
  };
  if (storage) onCalculated = setupHistory(form, storage, { edited, recalculate });
}

if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", init);
}
