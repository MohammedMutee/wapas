"""Persistence.

Three conventions run through every table here, and each one exists because
the alternative would quietly corrupt the number this project is judged on:

**Money is ``BigInteger`` paise.** No ``NUMERIC``, no ``FLOAT``. The type
system carries the unit in the column name (``*_paise``) so a misuse is visible
at the call site.

**Timestamps are ``TIMESTAMPTZ``, always.** A naive timestamp in a system that
reasons about quiet hours in IST and stores UTC is a latent compliance bug.

**The audit table is append-only in the database, not merely in convention.**
A trigger rejects ``UPDATE`` and ``DELETE``. The tamper-evidence guarantee does
not rest on application code being well-behaved.
"""

from .base import Base
from .erasure import ErasureResult, erase_counterparty
from .models import (
    Action,
    AuditEntryRow,
    CostEntry,
    Counterparty,
    Decision,
    Episode,
    Outcome,
    RiskEventRow,
)
from .session import session_scope, sessionmaker_for

__all__ = [
    "Action",
    "AuditEntryRow",
    "Base",
    "CostEntry",
    "Counterparty",
    "Decision",
    "Episode",
    "ErasureResult",
    "Outcome",
    "RiskEventRow",
    "erase_counterparty",
    "session_scope",
    "sessionmaker_for",
]
