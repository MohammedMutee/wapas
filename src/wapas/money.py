"""Money handling.

**Every monetary amount in Wapas is an integer number of paise.** Floats are
never used for money anywhere in the codebase — not in the database, not in the
domain models, not in the evaluation harness. A rounding error in a recovery
figure would undermine the one number the whole project is judged on.

Rupee values appear only at the presentation boundary (``format_inr``) and when
reading human-authored config (``rupees_to_paise``).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import NewType

Paise = NewType("Paise", int)
"""An integer count of paise. 100 paise = ₹1."""

ZERO = Paise(0)


def rupees_to_paise(rupees: str | int | float | Decimal) -> Paise:
    """Convert a rupee amount to paise, rounding half-up at the paisa.

    Accepts ``float`` only as a convenience for config and test literals; it is
    routed through :class:`~decimal.Decimal` immediately so no float arithmetic
    is ever performed on a monetary value.

    >>> rupees_to_paise("2499.50")
    249950
    >>> rupees_to_paise(1)
    100
    """
    d = Decimal(str(rupees))
    return Paise(int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))


def paise_to_rupees(paise: Paise | int) -> Decimal:
    """Exact rupee value of a paise amount, as a :class:`~decimal.Decimal`."""
    return (Decimal(int(paise)) / 100).quantize(Decimal("0.01"))


def format_inr(paise: Paise | int, *, compact: bool = False) -> str:
    """Render paise as an Indian-format rupee string.

    Uses the Indian digit grouping (lakh/crore), which is what a Razorpay
    reviewer expects to read.

    >>> format_inr(12345678)
    '₹1,23,456.78'
    >>> format_inr(12345678, compact=True)
    '₹1.23L'
    """
    value = paise_to_rupees(paise)
    negative = value < 0
    value = abs(value)

    if compact:
        if value >= 10_000_000:
            s = f"₹{value / 10_000_000:.2f}Cr"
        elif value >= 100_000:
            s = f"₹{value / 100_000:.2f}L"
        elif value >= 1_000:
            s = f"₹{value / 1_000:.1f}K"
        else:
            s = f"₹{value:.0f}"
        return f"-{s}" if negative else s

    whole, _, frac = f"{value:.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    s = f"₹{whole}.{frac}"
    return f"-{s}" if negative else s
