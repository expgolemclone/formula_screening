"""Tests for shared indicator functions."""

from __future__ import annotations

import pytest

from formula_screening.indicators import croic, fcf_yield_avg


def _make_stock(
    *,
    market_cap: float | None = 10000.0,
    cf_history: list[tuple[str, dict[str, float | None]]] | None = None,
    operating_cf: float | None = None,
    investing_cf: float | None = None,
    free_cf: float | None = None,
    stockholders_equity: float | None = None,
    short_term_debt: float | None = None,
    long_term_debt: float | None = None,
) -> dict:
    return {
        "metrics": {"market_cap": market_cap},
        "cf": {
            "operating_cf": operating_cf,
            "investing_cf": investing_cf,
            "free_cf": free_cf,
        },
        "bs": {
            "stockholders_equity": stockholders_equity,
            "short_term_debt": short_term_debt,
            "long_term_debt": long_term_debt,
        },
        "cf_history": cf_history or [],
    }


# --- fcf_yield_avg ---


class TestFcfYieldAvg:
    def test_basic_calculation(self) -> None:
        # Arrange
        stock: dict = _make_stock(
            market_cap=10000.0,
            cf_history=[
                ("2024-03", {"operating_cf": 200.0, "investing_cf": -100.0, "free_cf": None}),
                ("2023-03", {"operating_cf": 300.0, "investing_cf": -100.0, "free_cf": None}),
            ],
        )

        # Act
        result: float | None = fcf_yield_avg(stock)

        # Assert — (100 + 200) / 2 / 10000 = 0.015
        assert result == pytest.approx(0.015)

    def test_prefers_free_cf_when_available(self) -> None:
        # Arrange
        stock: dict = _make_stock(
            market_cap=10000.0,
            cf_history=[
                ("2024-03", {"operating_cf": 999.0, "investing_cf": -999.0, "free_cf": 500.0}),
            ],
        )

        # Act
        result: float | None = fcf_yield_avg(stock)

        # Assert — free_cf=500 takes priority, not operating+investing
        assert result == pytest.approx(500.0 / 10000.0)

    def test_returns_none_when_no_market_cap(self) -> None:
        # Arrange
        stock: dict = _make_stock(
            market_cap=None,
            cf_history=[("2024-03", {"operating_cf": 100.0, "investing_cf": -50.0, "free_cf": None})],
        )

        # Act & Assert
        assert fcf_yield_avg(stock) is None

    def test_returns_none_when_zero_market_cap(self) -> None:
        # Arrange
        stock: dict = _make_stock(
            market_cap=0.0,
            cf_history=[("2024-03", {"operating_cf": 100.0, "investing_cf": -50.0, "free_cf": None})],
        )

        # Act & Assert
        assert fcf_yield_avg(stock) is None

    def test_returns_none_when_no_cf_history(self) -> None:
        # Arrange
        stock: dict = _make_stock(market_cap=10000.0, cf_history=[])

        # Act & Assert
        assert fcf_yield_avg(stock) is None

    def test_skips_periods_with_missing_cf_data(self) -> None:
        # Arrange
        stock: dict = _make_stock(
            market_cap=10000.0,
            cf_history=[
                ("2024-03", {"operating_cf": 200.0, "investing_cf": -100.0, "free_cf": None}),
                ("2023-03", {"operating_cf": None, "investing_cf": None, "free_cf": None}),
            ],
        )

        # Act
        result: float | None = fcf_yield_avg(stock)

        # Assert — only 1 valid period: 100 / 10000
        assert result == pytest.approx(0.01)

    def test_returns_none_when_all_periods_have_missing_data(self) -> None:
        # Arrange
        stock: dict = _make_stock(
            market_cap=10000.0,
            cf_history=[
                ("2024-03", {"operating_cf": None, "investing_cf": None, "free_cf": None}),
            ],
        )

        # Act & Assert
        assert fcf_yield_avg(stock) is None


# --- croic ---


class TestCroic:
    def test_basic_calculation(self) -> None:
        # Arrange
        stock: dict = _make_stock(
            free_cf=100.0,
            stockholders_equity=800.0,
            short_term_debt=100.0,
            long_term_debt=100.0,
        )

        # Act
        result: float | None = croic(stock)

        # Assert — 100 / (800 + 100 + 100) = 0.1
        assert result == pytest.approx(0.1)

    def test_computes_fcf_from_operating_and_investing(self) -> None:
        # Arrange
        stock: dict = _make_stock(
            operating_cf=200.0,
            investing_cf=-50.0,
            free_cf=None,
            stockholders_equity=1000.0,
        )

        # Act
        result: float | None = croic(stock)

        # Assert — (200 + -50) / 1000 = 0.15
        assert result == pytest.approx(0.15)

    def test_returns_none_when_no_fcf(self) -> None:
        # Arrange
        stock: dict = _make_stock(
            free_cf=None,
            operating_cf=None,
            investing_cf=None,
            stockholders_equity=1000.0,
        )

        # Act & Assert
        assert croic(stock) is None

    def test_returns_none_when_no_equity(self) -> None:
        # Arrange
        stock: dict = _make_stock(free_cf=100.0, stockholders_equity=None)

        # Act & Assert
        assert croic(stock) is None

    def test_returns_none_when_invested_capital_is_zero(self) -> None:
        # Arrange
        stock: dict = _make_stock(free_cf=100.0, stockholders_equity=0.0)

        # Act & Assert
        assert croic(stock) is None

    def test_treats_missing_debt_as_zero(self) -> None:
        # Arrange
        stock: dict = _make_stock(
            free_cf=100.0,
            stockholders_equity=500.0,
            short_term_debt=None,
            long_term_debt=None,
        )

        # Act
        result: float | None = croic(stock)

        # Assert — 100 / 500 = 0.2
        assert result == pytest.approx(0.2)
