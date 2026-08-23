"""What the model is required to return.

Two properties matter more than the field list.

**The root cause is a closed enum.** A model that invents ``"bank_problem"``
fails validation, gets one retry with the error fed back, and then the caller
degrades to the rules classifier. There is no path by which an unrecognised
cause reaches the planner, because the planner selects a playbook by cause and
an unknown key there would be a silent no-op.

**Evidence is mandatory and quoted.** The model must point at the span of the
failure text it relied on. This is not decoration: it is what makes a wrong
diagnosis auditable after the fact, and asking for it measurably reduces
confident guessing on the uninformative cases.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..domain import RootCause


class DiagnosisResponse(BaseModel):
    """The model's answer, before it is trusted by anything."""

    model_config = ConfigDict(extra="forbid")

    signal_quality: Literal["specific", "weak", "generic"] = Field(
        description=(
            "Judge the failure text BEFORE choosing a cause. 'specific' when it "
            "names a mechanism — a balance, a card state, an authentication step, "
            "a bank's availability, a limit, a risk decision, a mandate state. "
            "'weak' when it hints at one without naming it. 'generic' when it "
            "says only that something failed."
        )
    )
    root_cause: RootCause = Field(
        description="The single most likely root cause, from the fixed taxonomy."
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description=(
            "Probability the cause above is correct. Use a low value when the "
            "failure text carries no diagnostic signal; a confident wrong answer "
            "is worse than an honest 0.3."
        ),
    )
    evidence: list[str] = Field(
        default_factory=list, max_length=4,
        description="Short quotes from the failure signal that support the cause.",
    )
    recoverable: bool = Field(
        description="Whether any action could plausibly still recover this payment."
    )
    recommended_horizon_hours: int = Field(
        ge=0, le=2160,
        description="How long it is worth working this episode before stopping.",
    )
    alternative_cause: RootCause | None = Field(
        default=None,
        description=(
            "The next most likely cause, when the signal is genuinely ambiguous. "
            "Null when the text is unambiguous."
        ),
    )
    notes: str = Field(default="", max_length=280)

    @model_validator(mode="after")
    def _confidence_must_match_the_evidence(self) -> Self:
        """Refuse an answer more confident than its own stated evidence allows.

        The model is asked to grade the failure text first and choose a cause
        second. If it grades the text as carrying no mechanism and then claims
        near-certainty about which mechanism it was, those two statements
        contradict each other, and the contradiction is machine-checkable.

        Raising here is not a rejection of the answer. ``ask_structured`` feeds
        the message back and asks again, and models are markedly better at
        fixing a named inconsistency than at avoiding it. It exists because the
        live endpoint, told in prose to be careful here, answered
        "Transaction declined" with `gateway_error` at 0.95 anyway. An
        instruction the model can ignore is not a control.
        """
        ceilings = {"generic": 0.5, "weak": 0.75}
        ceiling = ceilings.get(self.signal_quality)
        if ceiling is not None and self.confidence > ceiling:
            raise ValueError(
                f"signal_quality={self.signal_quality!r} caps confidence at "
                f"{ceiling}, but you returned {self.confidence}. Either the text "
                f"names a specific mechanism — in which case quote it and grade "
                f"the signal 'specific' — or it does not, in which case lower the "
                f"confidence and consider whether 'unknown' is the honest answer."
            )
        return self
