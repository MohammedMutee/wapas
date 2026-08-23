"""Evidence tampering: make the record say something that did not happen.

The audit chain is the project's claim to being verifiable rather than merely
plausible. These scenarios attack that claim directly — edit a payload, reorder
two entries, delete one, back-date one — and assert that verification catches
each. A tamper-evident log that quietly accepts an edit is worse than no log,
because it launders the edit.

Also here: the environment-level guarantees. A live Razorpay key must stop the
process rather than be handled carefully, and a policy file that tries to
disable an invariant must fail at load rather than at 2 a.m. inside an episode.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt

from pydantic import ValidationError

from redteam.scenarios import Scenario
from wapas.audit import HashChain, verify_chain
from wapas.clock import IST

T0 = _dt.datetime(2026, 6, 10, 9, 0, tzinfo=IST)


def _chain() -> HashChain:
    chain = HashChain(salt="redteam")
    for i in range(6):
        chain.append(at=T0 + _dt.timedelta(minutes=i), actor="system",
                     event_type="action_executed",
                     payload={"ref": f"ep_{i}", "amount_paise": 1000 * (i + 1)})
    return chain


def _edited_payload() -> tuple[bool, str]:
    chain = _chain()
    chain.entries[3] = dataclasses.replace(
        chain.entries[3], payload={"ref": "ep_3", "amount_paise": 1}
    )
    result = verify_chain(chain)
    return not result.ok, str(result)


def _reordered() -> tuple[bool, str]:
    chain = _chain()
    chain.entries[2], chain.entries[3] = chain.entries[3], chain.entries[2]
    result = verify_chain(chain)
    return not result.ok, str(result)


def _deleted() -> tuple[bool, str]:
    chain = _chain()
    del chain.entries[2]
    result = verify_chain(chain)
    return not result.ok, str(result)


def _backdated() -> tuple[bool, str]:
    chain = _chain()
    chain.entries[4] = dataclasses.replace(
        chain.entries[4], at=T0 - _dt.timedelta(days=30)
    )
    result = verify_chain(chain)
    return not result.ok, str(result)


def _intact_chain_verifies() -> tuple[bool, str]:
    """The control. A checker that rejects everything catches nothing."""
    result = verify_chain(_chain())
    return result.ok, str(result)


def _live_key_refused() -> tuple[bool, str]:
    from pydantic import ValidationError

    from wapas.config import Settings

    try:
        Settings(razorpay_key_id="rzp_live_ABCDEFGHIJKLMN", _env_file=None)
    except (ValidationError, ValueError) as exc:
        return True, f"refused at load: {str(exc).splitlines()[-1].strip()[:70]}"
    return False, "a live key was accepted"


def _invariant_cannot_be_configured_away() -> tuple[bool, str]:
    from wapas.policy.config import EscalationPolicy, MoneyActionPolicy, ThirdPartyPolicy

    attempts = [
        ("mandate requirement", lambda: MoneyActionPolicy(
            require_valid_mandate_for_debit=False, max_retries_per_payment=3,
            min_gap_between_retries_hours=4, never_retry_causes=(),
            verify_before_retry_causes=())),
        ("rung skipping", lambda: EscalationPolicy(
            version="x", skip_rungs=True, reset_on=(), ladder=())),
        ("third-party contact", lambda: ThirdPartyPolicy(
            contact_non_debtor_parties=True, disclose_debt_to_third_party=True)),
    ]
    survived = []
    for name, build in attempts:
        try:
            build()
        except (ValidationError, ValueError):
            continue
        survived.append(name)
    if survived:
        return False, f"configured away: {', '.join(survived)}"
    return True, f"all {len(attempts)} invariants refused at load"


TAMPER_SCENARIOS: list[Scenario] = [
    Scenario("audit-edit", "Change the amount on a recorded action.",
             "Verification fails.",
             "A log that accepts edits launders them.", _edited_payload),
    Scenario("audit-reorder", "Swap two entries to change the sequence of events.",
             "Verification fails.",
             "Order is often the whole story: was consent before or after contact?",
             _reordered),
    Scenario("audit-delete", "Remove the entry recording an action we regret.",
             "Verification fails.",
             "Deletion is the most likely real-world tampering.", _deleted),
    Scenario("audit-backdate", "Move an entry earlier to sit inside allowed hours.",
             "Verification fails.",
             "Back-dating is how a quiet-hours breach would be hidden.", _backdated),
    Scenario("audit-control", "Present an untampered chain.",
             "Verification passes. A checker that rejects everything catches nothing.",
             "Without this the four above prove nothing.", _intact_chain_verifies),
    Scenario("live-key", "Start the system with a live Razorpay key.",
             "Refused at configuration load.",
             "There is no live code path here, and a key that implies one must "
             "stop the process rather than be handled carefully.", _live_key_refused),
    Scenario("invariant-config", "Edit policy YAML to disable a hard invariant.",
             "Refused at load, not at 2 a.m. inside an episode.",
             "An invariant that is a setting is not an invariant.",
             _invariant_cannot_be_configured_away),
]
