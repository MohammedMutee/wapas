"""Property-based tests for the policy gate.

The scenario tests in ``test_policy_gate.py`` prove the gate blocks the twenty
attacks we thought of. These tests prove something stronger: across thousands
of *machine-generated* action sequences, no reachable sequence of gate-approved
actions can violate the invariants.

That is the difference between "we tested our guardrails" and "our guardrails
are guardrails".
"""

from __future__ import annotations

import datetime as _dt

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from wapas.clock import IST
from wapas.domain import (
    ALWAYS_ALLOWED,
    CONTACT_ACTIONS,
    Channel,
    GateVerdict,
    ProposedAction,
    RootCause,
    Surface,
    Tool,
)
from wapas.money import Paise
from wapas.policy import load_policies
from wapas.policy.gate import ContactRecord, GateContext, PolicyGate

POLICIES = load_policies("policies")
GATE = PolicyGate(POLICIES)
CAPS = POLICIES.contact.frequency_caps
MONEY = POLICIES.money.money_actions
START = _dt.datetime(2026, 9, 1, 9, 0, tzinfo=IST)

channels = st.sampled_from([Channel.WHATSAPP, Channel.SMS, Channel.EMAIL, Channel.VOICE])
tools = st.sampled_from(list(Tool))
causes = st.sampled_from(list(RootCause))


@st.composite
def proposals(draw) -> ProposedAction:
    """An arbitrary action an adversarial or buggy planner might emit."""
    tool = draw(tools)
    return ProposedAction(
        tool=tool,
        args={
            "channel": draw(channels).value,
            "rung": draw(st.integers(min_value=1, max_value=8)),
            "value_paise": draw(st.integers(min_value=0, max_value=10_000_00)),
            "human_approved": draw(st.booleans()),
            "recipient_is_third_party": draw(st.booleans()),
        },
        scheduled_for=None,
    )


def _run(actions: list[ProposedAction], ctx: GateContext) -> tuple[list, GateContext]:
    """Feed a sequence through the gate, applying only what it permits.

    This mirrors what the engine does: an allowed action consumes budget and
    advances the escalation rung; a denied one changes nothing but the log.
    """
    executed = []
    for proposal in actions:
        decision = GATE.evaluate(proposal, ctx)
        if decision.verdict is GateVerdict.DENY:
            continue
        action = decision.action
        assert action is not None
        executed.append((action, decision, ctx))

        is_contact = action.tool in CONTACT_ACTIONS
        # Exits are ungated, and they also do not consume the action budget: an
        # episode must always be able to close itself, however constrained it is.
        consumes_budget = action.tool not in ALWAYS_ALLOWED
        at = action.scheduled_for or ctx.now
        ctx = GateContext(
            **{
                **{f: getattr(ctx, f) for f in ctx.__slots__},
                "actions_used": ctx.actions_used + (1 if consumes_budget else 0),
                "contacts_used": ctx.contacts_used + (1 if is_contact else 0),
                "retries_used": ctx.retries_used + (1 if action.tool is Tool.RETRY_PAYMENT else 0),
                "last_retry_at": at if action.tool is Tool.RETRY_PAYMENT else ctx.last_retry_at,
                "contact_history": (
                    (*ctx.contact_history, ContactRecord(at=at, channel=_ch(action)))
                    if is_contact
                    else ctx.contact_history
                ),
                "escalation_rung": (
                    max(ctx.escalation_rung, int(action.args.get("rung", 0)))
                    if is_contact
                    else ctx.escalation_rung
                ),
                "last_rung_at": at if is_contact else ctx.last_rung_at,
            }
        )
    return executed, ctx


def _ch(action: ProposedAction) -> Channel:
    try:
        return Channel(action.args.get("channel"))
    except ValueError:
        return Channel.NONE


def _base(**overrides) -> GateContext:
    return GateContext(
        **{
            "now": START,
            "surface": Surface.PAYMENT,
            "root_cause": RootCause.INSUFFICIENT_FUNDS,
            "amount_paise": Paise(249900),
            "channel_consent": frozenset(
                {Channel.WHATSAPP, Channel.SMS, Channel.EMAIL, Channel.VOICE}
            ),
            "has_valid_mandate": True,
            "is_business": True,
            "ledger_verified": True,
            **overrides,
        }
    )


SETTINGS = settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow], deadline=None)


@SETTINGS
@given(st.lists(proposals(), min_size=1, max_size=15), causes)
def test_contact_cap_can_never_be_exceeded(actions, cause):
    _, final = _run(actions, _base(root_cause=cause))
    assert final.contacts_used <= CAPS.contacts_per_episode


@SETTINGS
@given(st.lists(proposals(), min_size=1, max_size=15), causes)
def test_action_budget_can_never_be_exceeded(actions, cause):
    _, final = _run(actions, _base(root_cause=cause))
    assert final.actions_used <= POLICIES.money.budgets.max_actions_per_episode, (
        "budget covers gated actions only; escalate/close are exits and are exempt"
    )


@SETTINGS
@given(st.lists(proposals(), min_size=1, max_size=15), causes)
def test_no_contact_ever_lands_inside_quiet_hours(actions, cause):
    """Every permitted contact executes outside the channel's quiet window."""
    executed, _ = _run(actions, _base(root_cause=cause, now=START))
    for action, _decision, ctx in executed:
        if action.tool not in CONTACT_ACTIONS:
            continue
        window = POLICIES.contact.quiet_hours.for_channel(_ch(action))
        at = (action.scheduled_for or ctx.now).astimezone(IST)
        assert not window.contains(at.timetz().replace(tzinfo=None)), (
            f"{action.tool} on {_ch(action)} scheduled at {at} inside quiet hours"
        )


@SETTINGS
@given(st.lists(proposals(), min_size=1, max_size=15), causes)
def test_no_debit_is_ever_presented_without_a_mandate(actions, cause):
    executed, _ = _run(actions, _base(root_cause=cause, has_valid_mandate=False))
    assert not any(a.tool is Tool.RETRY_PAYMENT for a, _, _ in executed)


@SETTINGS
@given(st.lists(proposals(), min_size=1, max_size=15))
def test_never_retry_causes_are_never_retried(actions):
    for cause in MONEY.never_retry_causes:
        executed, _ = _run(actions, _base(root_cause=cause))
        assert not any(a.tool is Tool.RETRY_PAYMENT for a, _, _ in executed), cause


@SETTINGS
@given(st.lists(proposals(), min_size=1, max_size=15), causes)
def test_retry_count_is_bounded(actions, cause):
    _, final = _run(actions, _base(root_cause=cause))
    assert final.retries_used <= MONEY.max_retries_per_payment


@SETTINGS
@given(st.lists(proposals(), min_size=1, max_size=15), causes)
def test_escalation_rungs_advance_by_at_most_one(actions, cause):
    """The ladder can never be climbed two rungs at a time."""
    ctx = _base(root_cause=cause)
    for proposal in actions:
        decision = GATE.evaluate(proposal, ctx)
        if decision.verdict is GateVerdict.DENY:
            continue
        action = decision.action
        assert action is not None
        if action.tool in CONTACT_ACTIONS:
            requested = int(action.args.get("rung", 0))
            assert requested <= ctx.escalation_rung + 1
            ctx = GateContext(
                **{
                    **{f: getattr(ctx, f) for f in ctx.__slots__},
                    "escalation_rung": max(ctx.escalation_rung, requested),
                    "last_rung_at": ctx.now,
                    "contacts_used": ctx.contacts_used + 1,
                    "actions_used": ctx.actions_used + 1,
                }
            )
        else:
            ctx = GateContext(
                **{**{f: getattr(ctx, f) for f in ctx.__slots__},
                   "actions_used": ctx.actions_used + 1}
            )


@SETTINGS
@given(st.lists(proposals(), min_size=1, max_size=15), causes)
def test_opt_out_suppresses_every_contact_forever(actions, cause):
    executed, _ = _run(actions, _base(root_cause=cause, opted_out=True))
    assert not any(a.tool in CONTACT_ACTIONS for a, _, _ in executed)


@SETTINGS
@given(st.lists(proposals(), min_size=1, max_size=15), causes)
def test_third_parties_are_never_contacted(actions, cause):
    executed, _ = _run(actions, _base(root_cause=cause))
    assert not any(
        a.args.get("recipient_is_third_party")
        for a, _, _ in executed
        if a.tool in CONTACT_ACTIONS
    )


@SETTINGS
@given(st.lists(proposals(), min_size=1, max_size=15), causes)
def test_kill_switch_permits_only_the_exits(actions, cause):
    executed, _ = _run(actions, _base(root_cause=cause, kill_switch_engaged=True))
    assert all(a.tool in {Tool.ESCALATE_TO_HUMAN, Tool.CLOSE_EPISODE} for a, _, _ in executed)


@SETTINGS
@given(st.lists(proposals(), min_size=1, max_size=15), causes)
def test_concessions_never_exceed_the_cap(actions, cause):
    ctx = _base(root_cause=cause)
    cap_pct = POLICIES.money.budgets.max_concession_pct_of_amount
    executed, _ = _run(actions, ctx)
    for action, _d, c in executed:
        if action.tool is Tool.OFFER_CONCESSION:
            assert int(action.args["value_paise"]) <= c.amount_paise * cap_pct // 100


@SETTINGS
@given(st.lists(proposals(), min_size=1, max_size=15), causes)
def test_exits_are_reachable_from_every_state(actions, cause):
    """However constrained the episode becomes, closing it stays possible."""
    _, final = _run(actions, _base(root_cause=cause))
    for tool in (Tool.CLOSE_EPISODE, Tool.ESCALATE_TO_HUMAN):
        assert GATE.evaluate(ProposedAction(tool=tool), final).verdict is GateVerdict.ALLOW


@SETTINGS
@given(st.lists(proposals(), min_size=1, max_size=10), causes)
def test_a_denied_action_never_carries_an_executable_payload(actions, cause):
    ctx = _base(root_cause=cause)
    for proposal in actions:
        d = GATE.evaluate(proposal, ctx)
        if d.verdict is GateVerdict.DENY:
            assert d.action is None and d.reasons


@SETTINGS
@given(st.lists(proposals(), min_size=1, max_size=10), causes)
def test_every_verdict_is_explained(actions, cause):
    """No silent decisions. The audit trail is only as good as its reasons."""
    ctx = _base(root_cause=cause)
    for proposal in actions:
        d = GATE.evaluate(proposal, ctx)
        assert d.reasons, f"{proposal.tool} produced {d.verdict} with no reason codes"
        assert d.policy_version == POLICIES.version
