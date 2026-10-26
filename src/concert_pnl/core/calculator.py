"""Расчёт P&L концерта по сценариям, точки безубыточности и риск-метрик.

Внутри считаем в полной точности Decimal, округляем только результат:
деньги — до копеек, доли — до 4 знаков (ROUND_HALF_UP).
"""

import math
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from .models import (
    ONE,
    ZERO,
    BreakEven,
    EventInput,
    PnLReport,
    Scenario,
    ScenarioResult,
    TaxMode,
    TaxRegime,
)

MONEY = Decimal("0.01")
RATIO = Decimal("0.0001")
_BISECTION_STEPS = 64


@dataclass(frozen=True)
class _RawPnL:
    tickets_sold: Decimal
    gross_revenue: Decimal
    vat: Decimal
    net_revenue: Decimal
    ticketing_fee: Decimal
    per_ticket_costs: Decimal
    fixed_costs: Decimal
    total_expenses: Decimal
    operating_profit: Decimal
    tax: Decimal
    net_profit: Decimal


def _round(value: Decimal, step: Decimal) -> Decimal:
    rounded = value.quantize(step, rounding=ROUND_HALF_UP)
    # -0.004 округляется в "-0.00": в JSON это выглядело бы как убыток
    return abs(rounded) if rounded == ZERO else rounded


def _money(value: Decimal) -> Decimal:
    return _round(value, MONEY)


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == ZERO:
        return None
    return _round(numerator / denominator, RATIO)


def _tax(regime: TaxRegime, net_revenue: Decimal, operating_profit: Decimal) -> Decimal:
    match regime.mode:
        case TaxMode.INCOME:
            return net_revenue * regime.rate
        case TaxMode.INCOME_MINUS_EXPENSES:
            return max(regime.rate * max(operating_profit, ZERO), regime.min_tax_rate * net_revenue)
        case TaxMode.PROFIT:
            return regime.rate * max(operating_profit, ZERO)


def _pnl(event: EventInput, sell_through: Decimal, *, whole_tickets: bool) -> _RawPnL:
    """P&L при заданной доле продаж.

    whole_tickets=True — реальный сценарий: билеты каждой категории округляются вниз.
    whole_tickets=False — непрерывная модель для поиска точки безубыточности.
    """
    sold = [t.quantity * sell_through for t in event.tiers]
    if whole_tickets:
        sold = [Decimal(math.floor(n)) for n in sold]

    tickets_sold = sum(sold, ZERO)
    gross_revenue = sum((t.price * n for t, n in zip(event.tiers, sold)), ZERO)
    vat_rate = event.tax.vat_rate
    vat = gross_revenue * vat_rate / (ONE + vat_rate)
    net_revenue = gross_revenue - vat

    ticketing_fee = gross_revenue * event.ticketing_fee_rate
    per_ticket_costs = tickets_sold * event.per_ticket_cost
    fixed_costs = sum((c.amount for c in event.fixed_costs), ZERO)
    total_expenses = ticketing_fee + per_ticket_costs + fixed_costs

    operating_profit = net_revenue - total_expenses
    tax = _tax(event.tax, net_revenue, operating_profit)

    return _RawPnL(
        tickets_sold=tickets_sold,
        gross_revenue=gross_revenue,
        vat=vat,
        net_revenue=net_revenue,
        ticketing_fee=ticketing_fee,
        per_ticket_costs=per_ticket_costs,
        fixed_costs=fixed_costs,
        total_expenses=total_expenses,
        operating_profit=operating_profit,
        tax=tax,
        net_profit=operating_profit - tax,
    )


def find_break_even(event: EventInput) -> BreakEven | None:
    """Минимальная доля продаж, при которой чистая прибыль >= 0.

    Структура продаж считается пропорциональной квотам категорий.
    None — не окупается даже при полном зале (в том числе когда каждый билет убыточен).
    """
    total = event.allocated_tickets

    def net_profit(s: Decimal) -> Decimal:
        return _pnl(event, s, whole_tickets=False).net_profit

    # Сначала полный зал: при нулевых постоянных расходах net(0) = 0,
    # и без этой проверки убыточные билеты дали бы «безубыточность с нуля»
    if net_profit(ONE) < ZERO:
        return None
    if net_profit(ZERO) >= ZERO:
        return BreakEven(sell_through=ZERO, tickets=0, gross_revenue=ZERO)

    low, high = ZERO, ONE
    for _ in range(_BISECTION_STEPS):
        mid = (low + high) / 2
        if net_profit(mid) >= ZERO:
            high = mid
        else:
            low = mid

    # high — двоичная дробь чуть выше точной точки; уточняем до целого билета,
    # иначе точка ровно в 600 билетов превратилась бы в 601
    tickets = math.floor(high * total)
    if net_profit(Decimal(tickets) / total) < ZERO:
        tickets += 1
    sell_through = Decimal(tickets) / total
    return BreakEven(
        sell_through=sell_through.quantize(RATIO, rounding=ROUND_CEILING),
        tickets=tickets,
        gross_revenue=_money(_pnl(event, sell_through, whole_tickets=False).gross_revenue),
    )


def _scenario_result(
    event: EventInput, scenario: Scenario, break_even: BreakEven | None
) -> ScenarioResult:
    raw = _pnl(event, scenario.sell_through, whole_tickets=True)

    # По выручке, а не по билетам: при округлении вниз дорогие категории выпадают
    # первыми, и запас «по билетам» выходил положительным при убытке
    margin_of_safety = None
    if break_even is not None:
        margin_of_safety = _ratio(raw.gross_revenue - break_even.gross_revenue, raw.gross_revenue)

    return ScenarioResult(
        name=scenario.name,
        sell_through=scenario.sell_through,
        tickets_sold=int(raw.tickets_sold),
        gross_revenue=_money(raw.gross_revenue),
        vat=_money(raw.vat),
        net_revenue=_money(raw.net_revenue),
        ticketing_fee=_money(raw.ticketing_fee),
        per_ticket_costs=_money(raw.per_ticket_costs),
        fixed_costs=_money(raw.fixed_costs),
        total_expenses=_money(raw.total_expenses),
        operating_profit=_money(raw.operating_profit),
        tax=_money(raw.tax),
        net_profit=_money(raw.net_profit),
        margin=_ratio(raw.net_profit, raw.net_revenue),
        roi=_ratio(raw.net_profit, raw.total_expenses),
        margin_of_safety=margin_of_safety,
    )


def calculate(event: EventInput) -> PnLReport:
    break_even = find_break_even(event)
    results = tuple(_scenario_result(event, s, break_even) for s in event.scenarios)

    expected_net_profit = None
    if all(s.probability is not None for s in event.scenarios):
        expected_net_profit = _money(
            sum(
                (
                    s.probability * _pnl(event, s.sell_through, whole_tickets=True).net_profit
                    for s in event.scenarios
                ),
                ZERO,
            )
        )

    return PnLReport(
        scenarios=results,
        break_even=break_even,
        zero_sales_net_profit=_money(_pnl(event, ZERO, whole_tickets=True).net_profit),
        expected_net_profit=expected_net_profit,
    )
