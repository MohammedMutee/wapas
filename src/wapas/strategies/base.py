"""The strategy interface."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Protocol

from ..domain import Diagnosis, ProposedAction, RootCause, Surface
from ..money import Paise


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """What a strategy is allowed to know.

    Note what is *absent*: no latent counterparty traits, no ground-truth
    cause, no knowledge of whether this episode would have self-recovered.
    A strategy sees the observable failure signal and its own history, exactly
    as a production system would.
    """

    opened_at: _dt.datetime
    now: _dt.datetime
    surface: Surface
    amount_paise: Paise
    rail: str
    error_code: str
    error_description: str
    error_source: str
    error_step: str
    attempt_no: int
    is_business: bool

    diagnosis: Diagnosis | None = None
    step_no: int = 0
    actions_taken: int = 0
    contacts_made: int = 0
    last_outcome: str = ""


class Strategy(Protocol):
    """Produces the next action, or ``None`` to stop."""

    name: str

    def diagnose(self, ctx: StrategyContext) -> Diagnosis | None:
        """Classify the cause. ``None`` means this strategy does not diagnose."""
        ...

    def next_action(self, ctx: StrategyContext) -> ProposedAction | None:
        """The next action to propose, or ``None`` when the strategy is done."""
        ...


def unknown_diagnosis(cause: RootCause = RootCause.UNKNOWN) -> Diagnosis:
    return Diagnosis(
        root_cause=cause, confidence=0.0, evidence=[], recoverable=True,
        recommended_horizon_hours=24, notes="strategy does not diagnose",
    )
