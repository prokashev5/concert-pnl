"""Доменные модели калькулятора. Без зависимостей от фреймворков.

Все денежные величины и ставки — Decimal; float запрещён на входе.
Ставки задаются долями: 6% -> Decimal("0.06").
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

ZERO = Decimal(0)
ONE = Decimal(1)


class InputError(ValueError):
    pass


def _dec(value: Decimal | int, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise InputError(f"{name}: ожидается Decimal или int, получено {type(value).__name__}")
    result = Decimal(value)
    if not result.is_finite():
        raise InputError(f"{name}: значение должно быть конечным")
    return result


def _non_negative(value: Decimal, name: str) -> None:
    if value < ZERO:
        raise InputError(f"{name}: не может быть отрицательным")


def _rate(value: Decimal, name: str) -> None:
    if not ZERO <= value < ONE:
        raise InputError(f"{name}: ставка должна быть в диапазоне [0, 1)")


class TaxMode(StrEnum):
    INCOME = "income"  # УСН «Доходы»: ставка × выручка без НДС
    INCOME_MINUS_EXPENSES = "income_minus_expenses"  # УСН «Доходы минус расходы» с минимальным налогом
    PROFIT = "profit"  # ОСН: ставка × положительная прибыль


@dataclass(frozen=True)
class TaxRegime:
    mode: TaxMode
    rate: Decimal
    # НДС, включённый в цену билета; 0 — без НДС
    vat_rate: Decimal = ZERO
    # Минимальный налог от доходов, только для INCOME_MINUS_EXPENSES (УСН 15% -> 0.01)
    min_tax_rate: Decimal = ZERO

    def __post_init__(self) -> None:
        for name in ("rate", "vat_rate", "min_tax_rate"):
            value = _dec(getattr(self, name), name)
            _rate(value, name)
            object.__setattr__(self, name, value)
        if self.min_tax_rate and self.mode is not TaxMode.INCOME_MINUS_EXPENSES:
            raise InputError("min_tax_rate применим только к режиму income_minus_expenses")


@dataclass(frozen=True)
class TicketTier:
    name: str
    price: Decimal  # цена билета с НДС
    quantity: int  # мест, выделенных под категорию

    def __post_init__(self) -> None:
        price = _dec(self.price, f"{self.name}.price")
        _non_negative(price, f"{self.name}.price")
        object.__setattr__(self, "price", price)
        if self.quantity < 0:
            raise InputError(f"{self.name}.quantity: не может быть отрицательным")


@dataclass(frozen=True)
class CostItem:
    """Фиксированный расход: гонорар артиста, аренда, продакшн, маркетинг и т.п."""

    name: str
    amount: Decimal

    def __post_init__(self) -> None:
        amount = _dec(self.amount, f"{self.name}.amount")
        _non_negative(amount, f"{self.name}.amount")
        object.__setattr__(self, "amount", amount)


@dataclass(frozen=True)
class Scenario:
    name: str
    sell_through: Decimal  # доля проданных мест каждой категории, 0..1
    probability: Decimal | None = None

    def __post_init__(self) -> None:
        sell_through = _dec(self.sell_through, f"{self.name}.sell_through")
        if not ZERO <= sell_through <= ONE:
            raise InputError(f"{self.name}.sell_through: должен быть в диапазоне [0, 1]")
        object.__setattr__(self, "sell_through", sell_through)
        if self.probability is not None:
            probability = _dec(self.probability, f"{self.name}.probability")
            if not ZERO <= probability <= ONE:
                raise InputError(f"{self.name}.probability: должна быть в диапазоне [0, 1]")
            object.__setattr__(self, "probability", probability)


@dataclass(frozen=True)
class EventInput:
    capacity: int
    tiers: tuple[TicketTier, ...]
    fixed_costs: tuple[CostItem, ...]
    tax: TaxRegime
    scenarios: tuple[Scenario, ...]
    per_ticket_cost: Decimal = ZERO
    # Комиссия билетного оператора, доля от выручки с НДС
    ticketing_fee_rate: Decimal = ZERO
    comps: int = 0  # пригласительные: занимают места, выручки не дают

    def __post_init__(self) -> None:
        object.__setattr__(self, "tiers", tuple(self.tiers))
        object.__setattr__(self, "fixed_costs", tuple(self.fixed_costs))
        object.__setattr__(self, "scenarios", tuple(self.scenarios))

        per_ticket_cost = _dec(self.per_ticket_cost, "per_ticket_cost")
        _non_negative(per_ticket_cost, "per_ticket_cost")
        object.__setattr__(self, "per_ticket_cost", per_ticket_cost)

        fee_rate = _dec(self.ticketing_fee_rate, "ticketing_fee_rate")
        _rate(fee_rate, "ticketing_fee_rate")
        object.__setattr__(self, "ticketing_fee_rate", fee_rate)

        if self.capacity <= 0:
            raise InputError("capacity: должна быть больше нуля")
        if self.comps < 0:
            raise InputError("comps: не может быть отрицательным")
        if not self.tiers:
            raise InputError("tiers: нужна хотя бы одна категория билетов")
        if self.allocated_tickets + self.comps > self.capacity:
            raise InputError(
                f"Билеты ({self.allocated_tickets}) + пригласительные ({self.comps}) "
                f"превышают вместимость ({self.capacity})"
            )

        if not self.scenarios:
            raise InputError("scenarios: нужен хотя бы один сценарий")
        names = [s.name for s in self.scenarios]
        if len(set(names)) != len(names):
            raise InputError("scenarios: имена сценариев должны быть уникальными")
        probabilities = [s.probability for s in self.scenarios]
        if any(p is not None for p in probabilities):
            if any(p is None for p in probabilities):
                raise InputError("scenarios: вероятности заданы не для всех сценариев")
            if sum(probabilities) != ONE:
                raise InputError("scenarios: сумма вероятностей должна быть равна 1")

    @property
    def allocated_tickets(self) -> int:
        return sum(t.quantity for t in self.tiers)


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    sell_through: Decimal
    tickets_sold: int
    gross_revenue: Decimal  # выручка с НДС
    vat: Decimal
    net_revenue: Decimal  # выручка без НДС
    ticketing_fee: Decimal
    per_ticket_costs: Decimal
    fixed_costs: Decimal
    total_expenses: Decimal
    operating_profit: Decimal
    tax: Decimal
    net_profit: Decimal
    margin: Decimal | None  # net_profit / net_revenue
    roi: Decimal | None  # net_profit / total_expenses
    # Запас прочности: (выручка − выручка безубыточности) / выручка; < 0 — сценарий убыточен
    margin_of_safety: Decimal | None


@dataclass(frozen=True)
class BreakEven:
    sell_through: Decimal
    tickets: int
    gross_revenue: Decimal  # выручка с НДС в точке безубыточности


@dataclass(frozen=True)
class PnLReport:
    scenarios: tuple[ScenarioResult, ...]
    break_even: BreakEven | None  # None — не окупается даже при полном зале
    zero_sales_net_profit: Decimal  # результат, если не продано ни одного билета
    expected_net_profit: Decimal | None  # None, если вероятности сценариев не заданы
