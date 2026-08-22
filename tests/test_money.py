"""Money is integer paise, always. These tests are the guardrail on that."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from wapas.money import Paise, format_inr, paise_to_rupees, rupees_to_paise


@pytest.mark.parametrize(
    ("rupees", "expected"),
    [("2499.50", 249950), ("0.01", 1), (1, 100), (0, 0), ("1.005", 101), ("-12.34", -1234)],
)
def test_rupees_to_paise(rupees, expected):
    assert rupees_to_paise(rupees) == expected


@pytest.mark.parametrize(
    ("paise", "expected"),
    [
        (12345678, "₹1,23,456.78"),
        (100, "₹1.00"),
        (0, "₹0.00"),
        (999_00, "₹999.00"),
        (10_000_00, "₹10,000.00"),
        (1_00_00_000_00, "₹1,00,00,000.00"),
        (-123456, "-₹1,234.56"),
    ],
)
def test_indian_digit_grouping(paise, expected):
    """Lakh/crore grouping — a Razorpay reviewer reads ₹1,23,456 not ₹123,456."""
    assert format_inr(paise) == expected


def test_compact_form():
    assert format_inr(rupees_to_paise("420000"), compact=True) == "₹4.20L"
    assert format_inr(rupees_to_paise("15000000"), compact=True) == "₹1.50Cr"


@given(st.integers(min_value=-10**12, max_value=10**12))
def test_roundtrip_is_exact(paise: int):
    """No float ever touches a monetary value, so the round-trip is lossless."""
    assert rupees_to_paise(paise_to_rupees(Paise(paise))) == paise


@given(st.integers(min_value=0, max_value=10**11))
def test_formatting_never_loses_a_paisa(paise: int):
    rendered = format_inr(Paise(paise)).removeprefix("₹").replace(",", "")
    assert Decimal(rendered) == paise_to_rupees(Paise(paise))
