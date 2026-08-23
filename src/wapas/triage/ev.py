"""Should this episode be worked at all?

The question every other component assumes away. A recovery system that works
every episode is not maximising anything — it is just busy, and the cost of
being busy falls on people who did not ask to be contacted.

The calculation is deliberately not "expected revenue > 0". Almost everything
clears that bar: a 20% chance on a ₹1,000 invoice is ₹200 against a few paise
of SMS, so a revenue-only floor skips nothing and the ``ev_floor_paise``
setting sits in the config doing nothing — which is exactly where it was found.

What makes the decision real is putting the *externalities* on the same side of
the ledger as the channel spend:

    EV = P(recover) x amount
         - channel spend
         - P(opt out) x what losing a contactable customer costs

For a low-probability episode the third term dominates. Chasing someone with a
6% chance of paying, at a 5% chance of losing them as a reachable customer for
a year, is negative expected value *for the merchant* before it is anything
else — which is the version of the argument that survives a conversation with
someone who does not care about the ethics.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain import DISPOSITIONS, RootCause, Surface
from ..money import ZERO, Paise


@dataclass(frozen=True, slots=True)
class TriageDecision:
    work: bool
    expected_value_paise: int
    probability: float
    reason: str
    expected_recovery_paise: int = 0
    expected_cost_paise: int = 0
    expected_harm_paise: int = 0

    def describe(self) -> str:
        return (f"P(recover)={self.probability:.0%} x {self.expected_recovery_paise} "
                f"- cost {self.expected_cost_paise} - harm {self.expected_harm_paise} "
                f"= {self.expected_value_paise}")


def triage(
    *,
    cause: RootCause,
    surface: Surface,
    amount: Paise,
    is_business: bool,
    scorer,
    costs,
    ev_floor_paise: int,
    planned_contacts: int = 2,
    opt_out_hazard_per_contact: float = 0.06,
) -> TriageDecision:
    """Decide whether an episode is worth working, and say why either way.

    ``planned_contacts`` is what the playbook for this cause would actually do,
    so an episode is judged against the plan it would receive rather than an
    average one.
    """
    if not DISPOSITIONS[cause].recoverable:
        return TriageDecision(
            work=False, expected_value_paise=0, probability=0.0,
            reason=f"{cause} is not recoverable by any action",
        )

    estimate = scorer.estimate(cause=cause, surface=surface, amount=amount)
    p = estimate.probability

    expected_recovery = int(p * int(amount))
    unit = int(costs.channels.get("whatsapp", ZERO))
    expected_cost = unit * planned_contacts

    # The term that does the work. Compounded rather than multiplied, because
    # the hazard applies per contact and a plan with four of them is not twice
    # the risk of one with two.
    p_opt_out = 1.0 - (1.0 - opt_out_hazard_per_contact) ** max(0, planned_contacts)
    opt_out_cost = int(costs.externalities.opt_out_cost(amount, is_business=is_business))
    expected_harm = int(p_opt_out * opt_out_cost)

    ev = expected_recovery - expected_cost - expected_harm
    if ev < ev_floor_paise:
        return TriageDecision(
            work=False, expected_value_paise=ev, probability=p,
            reason=(f"expected value {ev} paise is below the floor of {ev_floor_paise}; "
                    f"P(recover)={p:.0%} [{estimate.level}] does not justify a "
                    f"{p_opt_out:.0%} chance of losing a contactable customer"),
            expected_recovery_paise=expected_recovery,
            expected_cost_paise=expected_cost, expected_harm_paise=expected_harm,
        )

    return TriageDecision(
        work=True, expected_value_paise=ev, probability=p,
        reason=f"expected value {ev} paise clears the floor [{estimate.level}]",
        expected_recovery_paise=expected_recovery,
        expected_cost_paise=expected_cost, expected_harm_paise=expected_harm,
    )
