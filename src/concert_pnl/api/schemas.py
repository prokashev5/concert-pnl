"""Схемы HTTP API. Деньги и ставки — Decimal, в JSON передаются строками.

Ставки — доли, как в core: 6% -> "0.06".
"""

from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from concert_pnl.core.models import (
    CostItem,
    EventInput,
    Scenario,
    TaxMode,
    TaxRegime,
    TicketTier,
)

MAX_AMOUNT = Decimal(10) ** 12
# Минимальный налог УСН «Доходы минус расходы» по НК РФ
USN_MIN_TAX_RATE = Decimal("0.01")


def _reject_float(value: Any) -> Any:
    if isinstance(value, float):
        raise ValueError("передавайте число строкой, например \"1500.50\"")
    return value


_NoFloat = BeforeValidator(_reject_float)
Money = Annotated[Decimal, _NoFloat, Field(ge=0, le=MAX_AMOUNT, decimal_places=2)]
# Ограничение знаков не даёт протащить "1E-30": иначе сумма вероятностей
# округляется до 1 в контексте Decimal, а ROI улетает за пределы точности
Rate = Annotated[Decimal, _NoFloat, Field(ge=0, lt=1, decimal_places=6)]
Share = Annotated[Decimal, _NoFloat, Field(ge=0, le=1, decimal_places=6)]
Name = Annotated[str, Field(min_length=1, max_length=64)]
Count = Annotated[int, Field(ge=0, le=1_000_000)]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TierIn(_Request):
    name: Name
    price: Money
    quantity: Count


class CostIn(_Request):
    name: Name
    amount: Money


class TaxIn(_Request):
    mode: TaxMode
    rate: Rate
    vat_rate: Rate = Decimal(0)
    # Не передан: 1% для income_minus_expenses, 0 для остальных режимов
    min_tax_rate: Rate | None = None

    def resolved_min_tax_rate(self) -> Decimal:
        if self.min_tax_rate is not None:
            return self.min_tax_rate
        if self.mode is TaxMode.INCOME_MINUS_EXPENSES:
            return USN_MIN_TAX_RATE
        return Decimal(0)


class ScenarioIn(_Request):
    name: Name
    sell_through: Share
    probability: Share | None = None


class CalculateRequest(_Request):
    capacity: Annotated[int, Field(gt=0, le=1_000_000)]
    comps: Count = 0
    tiers: Annotated[list[TierIn], Field(min_length=1, max_length=20)]
    fixed_costs: Annotated[list[CostIn], Field(max_length=50)] = []
    per_ticket_cost: Money = Decimal(0)
    ticketing_fee_rate: Rate = Decimal(0)
    tax: TaxIn
    scenarios: Annotated[list[ScenarioIn], Field(min_length=1, max_length=10)]

    def to_core(self) -> EventInput:
        """Бизнес-правила (вместимость, сумма вероятностей и т.п.) проверяет core — InputError."""
        return EventInput(
            capacity=self.capacity,
            comps=self.comps,
            tiers=tuple(TicketTier(t.name, t.price, t.quantity) for t in self.tiers),
            fixed_costs=tuple(CostItem(c.name, c.amount) for c in self.fixed_costs),
            per_ticket_cost=self.per_ticket_cost,
            ticketing_fee_rate=self.ticketing_fee_rate,
            tax=TaxRegime(
                mode=self.tax.mode,
                rate=self.tax.rate,
                vat_rate=self.tax.vat_rate,
                min_tax_rate=self.tax.resolved_min_tax_rate(),
            ),
            scenarios=tuple(
                Scenario(s.name, s.sell_through, s.probability) for s in self.scenarios
            ),
        )


class _Response(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ScenarioOut(_Response):
    name: str
    sell_through: Decimal
    tickets_sold: int
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
    margin: Decimal | None
    roi: Decimal | None
    margin_of_safety: Decimal | None


class BreakEvenOut(_Response):
    sell_through: Decimal
    tickets: int
    gross_revenue: Decimal


class ReportResponse(_Response):
    scenarios: list[ScenarioOut]
    break_even: BreakEvenOut | None
    zero_sales_net_profit: Decimal
    expected_net_profit: Decimal | None
