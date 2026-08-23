"""Scenario tests for the policy gate.

Each test corresponds to a row of the red-team table in the plan. The suite is
the evidence behind the claim "0 policy escapes".
"""

from __future__ import annotations

import datetime as _dt

import pytest

from wapas.clock import IST
from wapas.domain import (
    Channel,
    GateVerdict,
    ProposedAction,
    RootCause,
    Surface,
    Tool,
)
from wapas.money import Paise, rupees_to_paise
from wapas.policy import load_policies
from wapas.policy.gate import ContactRecord, GateContext, PolicyGate

POLICIES = load_policies("policies")


@pytest.fixture
def gate() -> PolicyGate:
    return PolicyGate(POLICIES)


def ctx(**overrides) -> GateContext:
    """A permissive baseline context; each test tightens exactly one thing."""
    base = dict(
        now=_dt.datetime(2026, 9, 1, 11, 0, tzinfo=IST),
        surface=Surface.PAYMENT,
        root_cause=RootCause.INSUFFICIENT_FUNDS,
        amount_paise=rupees_to_paise(2499),
        channel_consent=frozenset({Channel.WHATSAPP, Channel.SMS, Channel.EMAIL, Channel.VOICE}),
        has_valid_mandate=True,
        ledger_verified=True,
    )
    return GateContext(**(base | overrides))


def msg(**args) -> ProposedAction:
    return ProposedAction(tool=Tool.SEND_MESSAGE, args={"channel": "whatsapp", **args})


# ── the exits are never gated ────────────────────────────────────────────────


@pytest.mark.parametrize("tool", [Tool.ESCALATE_TO_HUMAN, Tool.CLOSE_EPISODE])
def test_exits_are_always_allowed(gate, tool):
    """An agent that can be prevented from stopping, or from asking for help, is a bug."""
    c = ctx(kill_switch_engaged=True, opted_out=True, actions_used=999)
    assert gate.evaluate(ProposedAction(tool=tool), c).verdict is GateVerdict.ALLOW


def test_kill_switch_blocks_everything_else(gate):
    d = gate.evaluate(msg(), ctx(kill_switch_engaged=True))
    assert d.verdict is GateVerdict.DENY and "kill_switch_engaged" in d.reasons


# ── money invariants ─────────────────────────────────────────────────────────


def test_no_debit_without_a_mandate(gate):
    """Invariant. No policy configuration can relax this."""
    d = gate.evaluate(ProposedAction(tool=Tool.RETRY_PAYMENT), ctx(has_valid_mandate=False))
    assert d.verdict is GateVerdict.DENY and "no_valid_mandate" in d.reasons


@pytest.mark.parametrize(
    "cause", [RootCause.CARD_EXPIRED_OR_INVALID, RootCause.RISK_DECLINED, RootCause.MANDATE_REVOKED]
)
def test_never_retry_causes_are_refused(gate, cause):
    """A risk decline is a decision, not an obstacle. Rail-shopping around it is abuse."""
    d = gate.evaluate(ProposedAction(tool=Tool.RETRY_PAYMENT), ctx(root_cause=cause))
    assert d.verdict is GateVerdict.DENY and "never_retry_cause" in d.reasons


def test_timeout_requires_capture_verification_first(gate):
    """Retrying an ambiguous timeout risks charging the customer twice."""
    c = ctx(root_cause=RootCause.TECHNICAL_TIMEOUT, capture_verified=False)
    d = gate.evaluate(ProposedAction(tool=Tool.RETRY_PAYMENT), c)
    assert d.verdict is GateVerdict.DENY
    assert "double_charge_risk" in d.reasons

    ok = gate.evaluate(ProposedAction(tool=Tool.RETRY_PAYMENT), ctx(
        root_cause=RootCause.TECHNICAL_TIMEOUT, capture_verified=True))
    assert ok.verdict is GateVerdict.ALLOW


def test_retry_cap(gate):
    d = gate.evaluate(ProposedAction(tool=Tool.RETRY_PAYMENT), ctx(retries_used=3))
    assert d.verdict is GateVerdict.DENY and "max_retries_reached" in d.reasons


def test_retry_too_soon_is_rescheduled_not_dropped(gate):
    """MODIFY beats DENY: the recovery still happens, and it happens legally."""
    last = _dt.datetime(2026, 9, 1, 10, 0, tzinfo=IST)
    d = gate.evaluate(ProposedAction(tool=Tool.RETRY_PAYMENT), ctx(last_retry_at=last))
    assert d.verdict is GateVerdict.MODIFY
    assert d.action.scheduled_for == last + _dt.timedelta(hours=4)


def test_concession_capped_at_ten_percent(gate):
    # Below the ₹5,000 human-approval threshold, so this isolates the cap rule.
    amount = rupees_to_paise(4000)
    over = ProposedAction(tool=Tool.OFFER_CONCESSION, args={"value_paise": rupees_to_paise(2400)})
    d = gate.evaluate(over, ctx(amount_paise=amount))
    assert d.verdict is GateVerdict.DENY and "concession_exceeds_cap" in d.reasons

    within = ProposedAction(tool=Tool.OFFER_CONCESSION, args={"value_paise": rupees_to_paise(350)})
    assert gate.evaluate(within, ctx(amount_paise=amount)).verdict is GateVerdict.ALLOW


def test_large_amounts_need_a_human(gate):
    big = ctx(amount_paise=rupees_to_paise(50000))
    a = ProposedAction(tool=Tool.OFFER_CONCESSION, args={"value_paise": rupees_to_paise(100)})
    assert "human_approval_required" in gate.evaluate(a, big).reasons

    approved = ProposedAction(
        tool=Tool.OFFER_CONCESSION,
        args={"value_paise": rupees_to_paise(100), "human_approved": True},
    )
    assert gate.evaluate(approved, big).verdict is GateVerdict.ALLOW


# ── consent and contact ──────────────────────────────────────────────────────


def test_opt_out_is_absolute(gate):
    d = gate.evaluate(msg(), ctx(opted_out=True))
    assert d.verdict is GateVerdict.DENY and "opted_out" in d.reasons


def test_dnd_registry_is_honoured(gate):
    assert gate.evaluate(msg(), ctx(on_dnd_registry=True)).verdict is GateVerdict.DENY


def test_channel_without_consent_is_refused(gate):
    d = gate.evaluate(msg(), ctx(channel_consent=frozenset({Channel.EMAIL})))
    assert d.verdict is GateVerdict.DENY and "no_channel_consent" in d.reasons


def test_third_parties_are_never_contacted(gate):
    d = gate.evaluate(msg(recipient_is_third_party=True), ctx())
    assert d.verdict is GateVerdict.DENY and "third_party_contact" in d.reasons


def test_open_dispute_pauses_collections(gate):
    assert gate.evaluate(msg(), ctx(dispute_open=True)).verdict is GateVerdict.DENY


def test_promise_to_pay_suppresses_contact(gate):
    until = _dt.datetime(2026, 9, 5, 0, 0, tzinfo=IST)
    d = gate.evaluate(msg(), ctx(active_promise_until=until))
    assert d.verdict is GateVerdict.DENY and "promise_to_pay_active" in d.reasons


def test_no_contact_when_the_failure_is_not_the_customers_fault(gate):
    """Issuer downtime: messaging the customer is noise, not recovery."""
    d = gate.evaluate(msg(), ctx(root_cause=RootCause.ISSUER_DOWN))
    assert d.verdict is GateVerdict.DENY
    assert any("contact_not_indicated" in r for r in d.reasons)


# ── quiet hours ──────────────────────────────────────────────────────────────


def test_late_night_message_is_moved_to_morning(gate):
    """The signature MODIFY case: 22:10 IST → 08:00 next morning."""
    late = _dt.datetime(2026, 9, 1, 22, 10, tzinfo=IST)
    d = gate.evaluate(msg(), ctx(now=late))
    assert d.verdict is GateVerdict.MODIFY
    moved = d.action.scheduled_for.astimezone(IST)
    assert (moved.day, moved.hour, moved.minute) == (2, 8, 0)


def test_early_morning_message_is_moved_to_same_day_open(gate):
    early = _dt.datetime(2026, 9, 1, 5, 30, tzinfo=IST)
    d = gate.evaluate(msg(), ctx(now=early))
    moved = d.action.scheduled_for.astimezone(IST)
    assert (moved.day, moved.hour) == (1, 8)


def test_voice_is_held_to_a_tighter_window_than_messaging(gate):
    """20:00 IST: a WhatsApp message is fine, a phone call is not.

    Note which rule stops the call. Rung 4 carries a ``business_hours``
    precondition (09:00-18:00) that is strictly tighter than the voice quiet
    window (19:00-08:00), so the precondition fires first and the call is
    DENIED rather than rescheduled. That is intentional — a collections call is
    the most intrusive action in the system and the ladder, not the clock,
    is the thing that authorises it.
    """
    evening = _dt.datetime(2026, 9, 1, 20, 0, tzinfo=IST)
    assert gate.evaluate(msg(rung=1), ctx(now=evening)).verdict is GateVerdict.ALLOW

    c = ctx(now=evening, amount_paise=rupees_to_paise(5000), is_business=True,
            escalation_rung=3, last_rung_at=evening - _dt.timedelta(days=10))
    voice = ProposedAction(tool=Tool.PLACE_VOICE_CALL, args={"channel": "voice", "rung": 4})
    d = gate.evaluate(voice, c)
    assert d.verdict is GateVerdict.DENY
    assert "precondition_failed:business_hours" in d.reasons


def test_quiet_windows_differ_by_channel(gate):
    """The underlying windows really are different, independent of the ladder."""
    at_2000 = _dt.time(20, 0)
    qh = POLICIES.contact.quiet_hours
    assert qh.for_channel(Channel.WHATSAPP).contains(at_2000) is False
    assert qh.for_channel(Channel.VOICE).contains(at_2000) is True


def test_voice_inside_business_hours_is_allowed(gate):
    afternoon = _dt.datetime(2026, 9, 1, 15, 0, tzinfo=IST)
    c = ctx(now=afternoon, amount_paise=rupees_to_paise(5000), is_business=True,
            escalation_rung=3, last_rung_at=afternoon - _dt.timedelta(days=10))
    voice = ProposedAction(tool=Tool.PLACE_VOICE_CALL, args={"channel": "voice", "rung": 4})
    assert gate.evaluate(voice, c).verdict is GateVerdict.ALLOW


# ── escalation ladder ────────────────────────────────────────────────────────


def test_rungs_cannot_be_skipped(gate):
    d = gate.evaluate(msg(rung=4), ctx(escalation_rung=1))
    assert d.verdict is GateVerdict.DENY and "skip_rungs_forbidden" in d.reasons


def test_rung_cooldown_is_enforced(gate):
    yesterday = _dt.datetime(2026, 8, 31, 11, 0, tzinfo=IST)
    d = gate.evaluate(msg(rung=2), ctx(escalation_rung=1, last_rung_at=yesterday))
    assert d.verdict is GateVerdict.DENY and "rung_cooldown_active" in d.reasons


def test_statement_of_account_is_b2b_only(gate):
    c = ctx(escalation_rung=2, is_business=False,
            last_rung_at=_dt.datetime(2026, 8, 20, tzinfo=IST))
    d = gate.evaluate(ProposedAction(tool=Tool.SEND_MESSAGE,
                                     args={"channel": "email", "rung": 3}), c)
    assert "precondition_failed:b2b_only" in d.reasons


def test_unknown_precondition_fails_closed(gate):
    """A typo in a policy file must never widen what the agent may do."""
    assert gate._precondition_met("typo_not_a_real_rule", ctx(), Channel.WHATSAPP) is False


# ── frequency ────────────────────────────────────────────────────────────────


def test_episode_contact_cap(gate):
    d = gate.evaluate(msg(), ctx(contacts_used=4))
    assert d.verdict is GateVerdict.DENY and "episode_contact_cap" in d.reasons


def test_daily_cap(gate):
    now = _dt.datetime(2026, 9, 1, 11, 0, tzinfo=IST)
    history = (ContactRecord(at=now - _dt.timedelta(hours=3), channel=Channel.WHATSAPP,
                             responded=True),)
    d = gate.evaluate(msg(), ctx(now=now, contact_history=history))
    assert d.verdict is GateVerdict.DENY and "daily_contact_cap" in d.reasons


def test_cooldown_after_silence(gate):
    now = _dt.datetime(2026, 9, 1, 11, 0, tzinfo=IST)
    history = (ContactRecord(at=now - _dt.timedelta(hours=30), channel=Channel.WHATSAPP,
                             responded=False),)
    d = gate.evaluate(msg(), ctx(now=now, contact_history=history))
    assert d.verdict is GateVerdict.DENY and "cooldown_after_no_response" in d.reasons


# ── budgets ──────────────────────────────────────────────────────────────────


def test_action_budget_exhausted(gate):
    assert "budget_actions_exhausted" in gate.evaluate(msg(), ctx(actions_used=8)).reasons


def test_spend_budget_exhausted(gate):
    assert "budget_spend_exhausted" in gate.evaluate(msg(), ctx(spend_paise=Paise(15000))).reasons


def test_org_daily_cap(gate):
    assert "org_daily_spend_cap" in gate.evaluate(
        msg(), ctx(org_spend_today_paise=Paise(2_000_000))).reasons


# ── audit properties of the verdict itself ───────────────────────────────────


def test_every_decision_records_the_policy_version(gate):
    """The audit log must be able to say which policy produced the ruling."""
    for c in (ctx(), ctx(opted_out=True), ctx(now=_dt.datetime(2026, 9, 1, 23, tzinfo=IST))):
        assert gate.evaluate(msg(), c).policy_version == POLICIES.version


def test_allows_record_what_was_checked(gate):
    """An allow is not a wave-through; the trail shows the action was examined."""
    d = gate.evaluate(msg(), ctx())
    assert d.verdict is GateVerdict.ALLOW
    assert {"opt_out", "channel_consent", "quiet_hours", "cap_episode"} <= set(d.reasons)


def test_denials_never_carry_an_executable_action(gate):
    d = gate.evaluate(msg(), ctx(opted_out=True))
    assert d.action is None


# ── uncertainty is information the gate can act on ───────────────────────────


def test_a_hesitant_diagnosis_naming_a_dead_card_blocks_the_retry(gate):
    """The clearest thing structured model output buys.

    A keyword table returns one label. A calibrated model returns a label, a
    confidence, and what else it might have been — and if the runner-up is
    something we must never retry, then re-presenting the payment is exactly
    the action we would refuse if we knew. The safe action under uncertainty is
    the one that is safe for every cause still in play.

    Measured cost of not having this rule: the model arm ran 66 forbidden
    retries per 1,000 episodes against the keyword arm's 48, because honest
    `unknown` answers routed to a playbook that retries.
    """
    decision = gate.evaluate(
        ProposedAction(tool=Tool.RETRY_PAYMENT, rationale="test"),
        ctx(root_cause=RootCause.UNKNOWN,
            alternative_cause=RootCause.CARD_EXPIRED_OR_INVALID,
            diagnosis_confidence=0.4),
    )
    assert decision.verdict is GateVerdict.DENY
    assert "alternative_cause_never_retryable" in decision.reasons


def test_a_confident_diagnosis_is_not_second_guessed(gate):
    """The rule must not fire on every mention of a runner-up.

    A model that is sure of a retryable cause has earned the retry, even if it
    politely listed an alternative. Blocking there would punish the model for
    volunteering information, which is the incentive this whole design is
    trying to avoid creating.
    """
    decision = gate.evaluate(
        ProposedAction(tool=Tool.RETRY_PAYMENT, rationale="test"),
        ctx(root_cause=RootCause.INSUFFICIENT_FUNDS,
            alternative_cause=RootCause.CARD_EXPIRED_OR_INVALID,
            diagnosis_confidence=0.93),
    )
    assert decision.verdict is not GateVerdict.DENY


def test_a_harmless_runner_up_does_not_block_anything(gate):
    decision = gate.evaluate(
        ProposedAction(tool=Tool.RETRY_PAYMENT, rationale="test"),
        ctx(root_cause=RootCause.UNKNOWN,
            alternative_cause=RootCause.ISSUER_DOWN,
            diagnosis_confidence=0.3),
    )
    assert decision.verdict is not GateVerdict.DENY
