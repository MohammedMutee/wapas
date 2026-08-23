"""The Wapas agent: a model for the judgement, deterministic code for the rest.

The division of labour is the point of the whole project, so it is worth being
explicit about what the model is and is not permitted to do.

**It diagnoses.** One narrow, checkable question — why did this fail? — with
its answer validated against a closed taxonomy before anything reads it.

**It does not choose actions, spend money, or contact anyone.** The action
sequence comes from the playbook library, which is data; the policy gate then
independently vets every step against contact, money and escalation rules that
the model never sees and cannot influence. A model that returns nonsense
degrades the recovery rate. It cannot cause a 3 a.m. phone call, a debit
against a revoked mandate, or a fourth message to someone who asked to be left
alone, because none of those are decisions it is allowed to make.

This is also what makes the ablation clean. ``baseline_rules`` runs the exact
same planner, gate, ledger and audit chain with a keyword classifier in place
of the model, so the difference between the two arms is the diagnosis and
nothing else.
"""

from __future__ import annotations

from ..domain import Diagnosis, ProposedAction
from ..money import ZERO, Paise
from ..plan import playbook_for
from .base import StrategyContext
from .rules import RulesOnly


class LLMAgent:
    """Model diagnosis, then the same deterministic planner every arm uses."""

    name = "llm_agent"

    def __init__(self, diagnoser) -> None:
        self.diagnoser = diagnoser
        self.rules = RulesOnly()
        self._cost: Paise = ZERO

    def diagnose(self, ctx: StrategyContext) -> Diagnosis:
        diagnosis = self.diagnoser.diagnose(ctx)
        self._cost = Paise(self._cost + self.diagnoser.drain_cost())
        return diagnosis

    def drain_cost(self) -> Paise:
        cost, self._cost = self._cost, ZERO
        return cost

    def next_action(self, ctx: StrategyContext) -> ProposedAction | None:
        diagnosis = ctx.diagnosis or self.diagnose(ctx)
        playbook = playbook_for(diagnosis.root_cause, ctx.surface)
        if ctx.step_no >= len(playbook.steps):
            return None
        return playbook.steps[ctx.step_no].to_action(ctx.opened_at, ctx.amount_paise)
