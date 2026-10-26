from decimal import Decimal as D

import pytest

from concert_pnl.core.calculator import calculate, find_break_even
from concert_pnl.core.models import (
    CostItem,
    EventInput,
    InputError,
    Scenario,
    TaxMode,
    TaxRegime,
    TicketTier,
)

USN_6 = TaxRegime(mode=TaxMode.INCOME, rate=D("0.06"))
FULL_HOUSE = (Scenario("full", D(1)),)


def make_event(**overrides) -> EventInput:
    """Эталон: 900 билетов (800 × 2000 + 100 × 5000), постоянные расходы 1 650 000,
    50 ₽ на билет, комиссия оператора 10% от выручки, УСН 6%."""
    params = dict(
        capacity=1000,
        comps=50,
        tiers=(TicketTier("standard", D(2000), 800), TicketTier("vip", D(5000), 100)),
        fixed_costs=(
            CostItem("artist", D(1_000_000)),
            CostItem("rent", D(300_000)),
            CostItem("production", D(200_000)),
            CostItem("marketing", D(150_000)),
        ),
        per_ticket_cost=D(50),
        ticketing_fee_rate=D("0.10"),
        tax=USN_6,
        scenarios=(
            Scenario("pessimistic", D("0.5"), D("0.25")),
            Scenario("base", D("0.8"), D("0.5")),
            Scenario("optimistic", D(1), D("0.25")),
        ),
    )
    params.update(overrides)
    return EventInput(**params)


def by_name(report):
    return {s.name: s for s in report.scenarios}


class TestScenarios:
    def test_base_scenario(self):
        base = by_name(calculate(make_event()))["base"]
        assert base.tickets_sold == 720
        assert base.gross_revenue == D("1680000.00")
        assert base.vat == D("0.00")
        assert base.ticketing_fee == D("168000.00")
        assert base.per_ticket_costs == D("36000.00")
        assert base.fixed_costs == D("1650000.00")
        assert base.total_expenses == D("1854000.00")
        assert base.operating_profit == D("-174000.00")
        assert base.tax == D("100800.00")  # УСН «Доходы» платится и при убытке
        assert base.net_profit == D("-274800.00")
        assert base.margin == D("-0.1636")
        assert base.roi == D("-0.1482")

    def test_full_house(self):
        optimistic = by_name(calculate(make_event()))["optimistic"]
        assert optimistic.gross_revenue == D("2100000.00")
        assert optimistic.net_profit == D("69000.00")

    def test_tickets_rounded_down_per_tier(self):
        event = make_event(
            tiers=(TicketTier("a", D(100), 3), TicketTier("b", D(100), 3)),
            scenarios=(Scenario("half", D("0.5")),),
        )
        assert calculate(event).scenarios[0].tickets_sold == 2

    def test_ticketing_fee_charged_on_gross_including_vat(self):
        event = make_event(
            capacity=100,
            comps=0,
            tiers=(TicketTier("all", D(1220), 100),),
            fixed_costs=(CostItem("rent", D(50_000)),),
            per_ticket_cost=D(0),
            tax=TaxRegime(mode=TaxMode.PROFIT, rate=D("0.25"), vat_rate=D("0.22")),
            scenarios=FULL_HOUSE,
        )
        result = calculate(event).scenarios[0]
        assert result.gross_revenue == D("122000.00")
        assert result.vat == D("22000.00")
        assert result.net_revenue == D("100000.00")
        assert result.ticketing_fee == D("12200.00")
        assert result.operating_profit == D("37800.00")
        assert result.tax == D("9450.00")
        assert result.net_profit == D("28350.00")

    def test_profit_tax_is_zero_on_loss(self):
        event = make_event(tax=TaxRegime(mode=TaxMode.PROFIT, rate=D("0.25")))
        assert by_name(calculate(event))["base"].tax == D("0.00")


class TestUsnIncomeMinusExpenses:
    REGIME = TaxRegime(
        mode=TaxMode.INCOME_MINUS_EXPENSES, rate=D("0.15"), min_tax_rate=D("0.01")
    )

    def make(self, fixed: int) -> EventInput:
        return make_event(
            capacity=100,
            comps=0,
            tiers=(TicketTier("all", D(1000), 100),),
            fixed_costs=(CostItem("rent", D(fixed)),),
            per_ticket_cost=D(0),
            ticketing_fee_rate=D(0),
            tax=self.REGIME,
            scenarios=FULL_HOUSE,
        )

    def test_regular_tax(self):
        result = calculate(self.make(50_000)).scenarios[0]
        assert result.tax == D("7500.00")

    def test_minimum_tax_when_profit_is_small(self):
        result = calculate(self.make(95_000)).scenarios[0]
        assert result.tax == D("1000.00")
        assert result.net_profit == D("4000.00")

    def test_minimum_tax_on_loss(self):
        result = calculate(self.make(200_000)).scenarios[0]
        assert result.tax == D("1000.00")
        assert result.net_profit == D("-101000.00")


class TestBreakEven:
    def test_break_even_point(self):
        # net(s) = 2 100 000·s·(1 − 0.10 − 0.06) − 45 000·s − 1 650 000 = 1 719 000·s − 1 650 000
        # s* = 0.95986…, 863.87 билета -> 864 = 96% от 900
        break_even = find_break_even(make_event())
        assert break_even.tickets == 864
        assert break_even.sell_through == D("0.9600")
        assert break_even.gross_revenue == D("2016000.00")

    def test_exact_integer_point_is_not_overshot(self):
        # 1000 × 1000 ₽, расходы 600 000 -> ровно 600 билетов, а не 601
        event = make_event(
            tiers=(TicketTier("all", D(1000), 1000),),
            comps=0,
            fixed_costs=(CostItem("rent", D(600_000)),),
            per_ticket_cost=D(0),
            ticketing_fee_rate=D(0),
            tax=TaxRegime(mode=TaxMode.PROFIT, rate=D(0)),
        )
        break_even = find_break_even(event)
        assert break_even.tickets == 600
        assert break_even.sell_through == D("0.6000")

    def test_loss_on_every_ticket_never_breaks_even(self):
        # Нет постоянных расходов, но билет за 2000 стоит 3000: net(0) = 0, дальше только хуже
        event = make_event(
            capacity=100,
            comps=0,
            tiers=(TicketTier("all", D(2000), 100),),
            fixed_costs=(),
            per_ticket_cost=D(3000),
            scenarios=(Scenario("half", D("0.5")),),
        )
        report = calculate(event)
        assert report.break_even is None
        assert report.scenarios[0].net_profit < 0
        assert report.scenarios[0].margin_of_safety is None

    def test_never_breaks_even(self):
        event = make_event(fixed_costs=(CostItem("artist", D(10_000_000)),))
        report = calculate(event)
        assert report.break_even is None
        assert all(s.margin_of_safety is None for s in report.scenarios)

    def test_profitable_without_sales(self):
        event = make_event(fixed_costs=(), per_ticket_cost=D(0))
        assert find_break_even(event).tickets == 0


class TestRiskMetrics:
    def test_margin_of_safety(self):
        scenarios = by_name(calculate(make_event()))
        assert scenarios["optimistic"].margin_of_safety == D("0.0400")
        assert scenarios["base"].margin_of_safety == D("-0.2000")

    def test_margin_of_safety_is_negative_when_expensive_tier_rounds_away(self):
        # VIP 3 × 50 000 при 32% продаж -> floor(0.96) = 0 VIP: билетов больше точки
        # безубыточности (32 > 31), но выручки меньше — запас должен быть отрицательным
        event = make_event(
            capacity=103,
            comps=0,
            tiers=(TicketTier("vip", D(50_000), 3), TicketTier("standard", D(1000), 100)),
            fixed_costs=(CostItem("rent", D(75_000)),),
            per_ticket_cost=D(0),
            ticketing_fee_rate=D(0),
            tax=TaxRegime(mode=TaxMode.PROFIT, rate=D(0)),
            scenarios=(Scenario("low", D("0.32")),),
        )
        result = calculate(event).scenarios[0]
        assert result.net_profit == D("-43000.00")
        assert result.margin_of_safety < 0

    def test_margin_of_safety_without_sales(self):
        event = make_event(scenarios=(Scenario("none", D(0)),))
        assert calculate(event).scenarios[0].margin_of_safety is None

    def test_expected_net_profit(self):
        # 0.25 × (−790 500) + 0.5 × (−274 800) + 0.25 × 69 000
        assert calculate(make_event()).expected_net_profit == D("-317775.00")

    def test_expected_net_profit_without_probabilities(self):
        event = make_event(scenarios=(Scenario("base", D("0.8")),))
        assert calculate(event).expected_net_profit is None

    def test_tiny_loss_is_not_negative_zero(self):
        # 1 билет за 1 ₽, расходы 1.004 ₽: прибыль −0.004 -> "0.00", а не "-0.00"
        event = make_event(
            capacity=1,
            comps=0,
            tiers=(TicketTier("all", D(1), 1),),
            fixed_costs=(CostItem("rent", D("1.004")),),
            per_ticket_cost=D(0),
            ticketing_fee_rate=D(0),
            tax=TaxRegime(mode=TaxMode.PROFIT, rate=D(0)),
            scenarios=FULL_HOUSE,
        )
        assert str(calculate(event).scenarios[0].net_profit) == "0.00"

    def test_zero_sales_loss_equals_fixed_costs(self):
        assert calculate(make_event()).zero_sales_net_profit == D("-1650000.00")


class TestValidation:
    def test_tickets_and_comps_exceed_capacity(self):
        with pytest.raises(InputError, match="вместимость"):
            make_event(comps=101)

    def test_float_rejected(self):
        with pytest.raises(InputError, match="float"):
            TicketTier("vip", 5000.0, 10)

    def test_rate_must_be_below_one(self):
        with pytest.raises(InputError):
            make_event(ticketing_fee_rate=D(1))

    def test_negative_cost_rejected(self):
        with pytest.raises(InputError):
            CostItem("rent", D(-1))

    def test_sell_through_out_of_range(self):
        with pytest.raises(InputError):
            Scenario("x", D("1.01"))

    def test_probabilities_must_sum_to_one(self):
        with pytest.raises(InputError, match="сумма"):
            make_event(scenarios=(Scenario("a", D("0.5"), D("0.5")), Scenario("b", D(1), D("0.4"))))

    def test_probabilities_all_or_none(self):
        with pytest.raises(InputError, match="не для всех"):
            make_event(scenarios=(Scenario("a", D("0.5"), D(1)), Scenario("b", D(1))))

    def test_duplicate_scenario_names(self):
        with pytest.raises(InputError, match="уникальными"):
            make_event(scenarios=(Scenario("a", D("0.5")), Scenario("a", D(1))))

    def test_min_tax_only_for_income_minus_expenses(self):
        with pytest.raises(InputError):
            TaxRegime(mode=TaxMode.INCOME, rate=D("0.06"), min_tax_rate=D("0.01"))
