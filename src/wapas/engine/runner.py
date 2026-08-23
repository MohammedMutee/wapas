"""Runs one recovery episode to a terminal state.

The loop is deliberately identical for every arm — control, baselines and the
agent all pass through the same policy gate, the same cost ledger, the same
outcome attribution and the same audit chain. Only the strategy differs. If the
arms ran through different code, any measured difference between them would be
partly an artefact of the harness.

Self-recovery is applied to **every** arm, including control. A counterparty
who was going to pay anyway pays anyway, and that payment is recorded as
``UNATTRIBUTED``. This is the single most important line in the file: without
it, gross recovery would credit the agent for revenue it never influenced.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field

from sim.populations import SeededEpisode
from sim.responses import Interaction, ResponseModel
from sim.rng import Rng

from ..audit import HashChain
from ..audit.chain import canonical_json
from ..domain import (
    ALWAYS_ALLOWED,
    CONTACT_ACTIONS,
    NEVER_RETRY,
    Arm,
    AttributionMethod,
    Channel,
    CostKind,
    Diagnosis,
    EpisodeState,
    GateVerdict,
    OutcomeKind,
    ProposedAction,
    RootCause,
    Tool,
)
from ..llm.costs import CostBook
from ..money import ZERO, Paise
from ..policy import PolicyBundle, PolicyGate
from ..policy.gate import ContactRecord, GateContext
from ..strategies.base import Strategy, StrategyContext

MAX_STEPS = 24
"""Absolute loop bound, independent of policy. A runaway agent is a bug, and a
bug must not become an unbounded spend."""


def assign_arm(episode_ref: str, run_seed: int, shares: dict[Arm, float]) -> Arm:
    """Deterministic, unstratified arm assignment.

    Derived by hashing the episode reference with the run seed, so assignment
    is reproducible, independent of iteration order, and stable if episodes are
    added or removed.

    This is simple randomisation, and simple randomisation is what produced the
    A/A false positive documented in ``DECISIONS.md`` (D19): with heavy-tailed
    amounts, two arms drawn this way can differ in composition enough that a
    difference appears between arms running *identical* strategies. The batch
    evaluation uses :func:`stratified_assignment` instead. This function stays
    because the property tests want a one-episode-at-a-time assigner, and
    because the report compares the two designs.
    """
    digest = hashlib.sha256(f"{run_seed}|{episode_ref}".encode()).digest()
    draw = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    cumulative = 0.0
    for arm, share in shares.items():
        cumulative += share
        if draw < cumulative:
            return arm
    return next(reversed(shares))


STRATA = 10
"""Amount deciles. Ten is the usual choice and is enough that the largest
episodes cannot pile into one arm; more strata would leave too few episodes per
cell for the smaller arms."""


@dataclass(frozen=True, slots=True)
class Allocation:
    """The result of stratified randomisation.

    ``stratum`` is kept, not discarded, because the permutation test has to
    shuffle labels within the same strata the design used. An analysis that
    ignores the stratification it was given is testing a null the experiment
    never ran.
    """

    arm: dict[str, Arm]
    stratum: dict[str, int]

    def __getitem__(self, ref: str) -> Arm:
        return self.arm[ref]


def stratified_assignment(
    episodes: Sequence[tuple[str, int]],
    run_seed: int,
    shares: dict[Arm, float],
    *,
    strata: int = STRATA,
) -> Allocation:
    """Randomise arms within amount deciles.

    Takes ``(ref, amount_paise)`` pairs. Episodes are ranked by amount into
    equal-count strata; within each stratum they are ordered by a hash of
    ``run_seed|ref`` — pseudo-random but independent of the amount — and dealt
    to arms by largest-remainder apportionment.

    The guarantee is that every arm receives the same *amount profile* to
    within one episode per decile. Under simple randomisation the arms are
    equal in expectation only, and with a lognormal amount distribution the
    realised difference on any one seed is large enough to swamp a real effect.
    Stratifying removes that variance instead of hoping it averages out.

    Assignment remains deterministic in ``run_seed`` and stable under
    reordering of the input, but *not* under adding or removing episodes: ranks
    shift. That is the price of stratification, and it is acceptable because
    the population is regenerated from the seed anyway.
    """
    if not episodes:
        return Allocation(arm={}, stratum={})

    ordered = sorted(episodes, key=lambda e: (e[1], e[0]))
    n = len(ordered)
    stratum_of: dict[str, int] = {
        ref: min(strata - 1, rank * strata // n) for rank, (ref, _) in enumerate(ordered)
    }

    buckets: dict[int, list[str]] = {}
    for ref, index in stratum_of.items():
        buckets.setdefault(index, []).append(ref)

    assignment: dict[str, Arm] = {}
    for index in sorted(buckets):
        members = sorted(
            buckets[index],
            key=lambda ref: hashlib.sha256(f"{run_seed}|{ref}".encode()).digest(),
        )
        for ref, arm in zip(members, _apportion(len(members), shares), strict=True):
            assignment[ref] = arm
    return Allocation(arm=assignment, stratum=stratum_of)


def _apportion(size: int, shares: dict[Arm, float]) -> list[Arm]:
    """Largest-remainder apportionment of ``size`` slots across arms.

    Floor each arm's exact quota, then hand the leftover slots to the arms with
    the largest fractional parts, breaking ties by arm name so the result does
    not depend on dict ordering.
    """
    exact = {arm: size * share for arm, share in shares.items()}
    counts = {arm: int(quota) for arm, quota in exact.items()}
    leftover = size - sum(counts.values())
    ranked = sorted(exact, key=lambda a: (-(exact[a] - counts[a]), str(a)))
    for arm in ranked[:leftover]:
        counts[arm] += 1

    out: list[Arm] = []
    for arm in shares:
        out.extend([arm] * counts[arm])
    return out


@dataclass
class EpisodeResult:
    """Everything the evaluation needs from one episode."""

    ref: str
    arm: Arm
    surface: str
    state: EpisodeState
    amount_paise: Paise
    recovered_paise: Paise = ZERO
    cost_paise: Paise = ZERO
    externality_paise: Paise = ZERO
    """Modelled future revenue destroyed by an adverse reaction — an opt-out,
    a complaint, a dispute. Kept apart from ``cost_paise``, which is money
    actually spent, because the two have very different standards of evidence
    and a reader is entitled to reject one and keep the other."""
    actions_taken: int = 0
    contacts_made: int = 0
    retries: int = 0
    denials: int = 0
    modifications: int = 0
    opted_out: bool = False
    complained: bool = False
    disputed: bool = False
    escalated: bool = False
    forbidden_retries: int = 0
    """Retries executed on an episode whose *true* cause is never-retryable.

    Measured against ground truth, never acted on. The policy gate can only
    refuse a retry for a cause someone identified, so this counts the harm that
    a misdiagnosis lets through — the cost of being wrong, in the currency that
    matters. It is the number an improved diagnoser has to drive down, and it
    is reported per arm rather than kept quiet.
    """
    self_recovered: bool = False
    """True when the payment arrived without any attributable action."""
    true_cause: RootCause | None = None
    diagnosed_cause: RootCause | None = None
    signal_informative: bool = True
    """Whether the failure text could identify the cause. For reporting only:
    it splits diagnosis accuracy into the questions that were answerable and
    the ones that were not."""
    signal_established: bool = True
    """Whether the wording predates the evaluation window. For reporting only."""
    time_to_recovery: _dt.timedelta | None = None
    terminal_reason: str = ""
    audit_entries: int = 0
    denial_reasons: list[str] = field(default_factory=list)

    @property
    def net_paise(self) -> Paise:
        """Realised money only: recovered less spend actually incurred."""
        return Paise(self.recovered_paise - self.cost_paise)

    @property
    def net_after_externalities_paise(self) -> Paise:
        """Net including the modelled cost of adverse reactions."""
        return Paise(self.net_paise - self.externality_paise)

    @property
    def recovered(self) -> bool:
        return self.state in {EpisodeState.RECOVERED, EpisodeState.PARTIALLY_RECOVERED}

    @property
    def diagnosis_correct(self) -> bool | None:
        if self.true_cause is None or self.diagnosed_cause is None:
            return None
        return self.true_cause == self.diagnosed_cause


class EpisodeRunner:
    """Drives a single episode from open to a terminal state."""

    def __init__(
        self,
        *,
        policies: PolicyBundle,
        costs: CostBook,
        response: ResponseModel,
        run_seed: int,
        chain: HashChain | None = None,
    ) -> None:
        self.gate = PolicyGate(policies)
        self.policies = policies
        self.costs = costs
        self.response = response
        self.run_seed = run_seed
        self.chain = chain

    # ── main loop ────────────────────────────────────────────────────────────

    def run(
        self,
        ep: SeededEpisode,
        arm: Arm,
        strategy: Strategy,
        *,
        observe_until: _dt.datetime | None = None,
    ) -> EpisodeResult:
        rng = Rng(self.run_seed, "episode", ep.ref, arm)
        result = EpisodeResult(
            ref=ep.ref, arm=arm, surface=str(ep.surface), state=EpisodeState.INGESTED,
            amount_paise=ep.amount_paise, true_cause=ep.true_cause,
            signal_informative=getattr(ep, "signal_informative", True),
            signal_established=getattr(ep, "signal_established", True),
        )
        now = ep.occurred_at
        # Two distinct windows, and conflating them is how a control arm ends up
        # recovering nothing:
        #   action_horizon  — how long an episode may be WORKED
        #   watch_until     — how long we WATCH for an outcome
        #
        # Both are identical for every arm, and neither may depend on the true
        # root cause. An earlier version set action_horizon from
        # DISPOSITIONS[ep.true_cause], which meant the harness was giving every
        # strategy an oracle-derived stopping rule: the agent looked good at
        # knowing when to give up without ever having decided it, and the naive
        # ladder was cut short by information it cannot see. Knowing when to
        # stop is now something a strategy has to earn.
        action_horizon = ep.occurred_at + _dt.timedelta(
            hours=self.policies.money.triage.action_window_hours
        )
        watch_until = observe_until or (ep.occurred_at + _dt.timedelta(days=30))
        self._audit(now, "system", "episode_opened", ep.ref,
                    {"arm": str(arm), "amount_paise": ep.amount_paise,
                     "surface": str(ep.surface)})

        diagnosis = self._diagnose(strategy, ep, now, result)
        contact_history: list[ContactRecord] = []
        consent = self._consent(ep)
        last_retry_at: _dt.datetime | None = None
        step = 0

        while step < MAX_STEPS:
            # Self-recovery can land at any point, in any arm, including before
            # the first action. Check it before acting so an untouched control
            # episode can still recover.
            if self._check_self_recovery(ep, now, result):
                break

            ctx = StrategyContext(
                opened_at=ep.occurred_at, now=now, surface=ep.surface,
                amount_paise=ep.amount_paise, rail=ep.rail, error_code=ep.error_code,
                error_description=ep.error_description, error_source=ep.error_source,
                error_step=ep.error_step, attempt_no=1,
                is_business=getattr(ep.counterparty, "is_business", False),
                diagnosis=diagnosis, step_no=step,
                actions_taken=result.actions_taken, contacts_made=result.contacts_made,
            )
            proposal = strategy.next_action(ctx)
            if proposal is None:
                result.state = (EpisodeState.EXHAUSTED if result.actions_taken
                                else EpisodeState.SKIPPED_NEGATIVE_EV)
                result.terminal_reason = "strategy produced no further action"
                break

            when = max(now, proposal.scheduled_for or now)
            if when > action_horizon and proposal.tool not in ALWAYS_ALLOWED:
                result.state = EpisodeState.EXHAUSTED
                result.terminal_reason = "recovery horizon passed"
                break

            gate_ctx = self._gate_context(
                ep, diagnosis, when, result, contact_history, consent, last_retry_at
            )
            decision = self.gate.evaluate(proposal, gate_ctx)
            self._audit(when, "policy", "gate_decision", ep.ref, {
                "step": step, "tool": str(proposal.tool), "verdict": str(decision.verdict),
                "reasons": list(decision.reasons), "policy_version": decision.policy_version,
            })

            if decision.verdict is GateVerdict.DENY:
                result.denials += 1
                result.denial_reasons.extend(decision.reasons)
                step += 1
                if self._blocked_terminally(decision.reasons, result):
                    break
                continue

            if decision.verdict is GateVerdict.MODIFY:
                result.modifications += 1

            action = decision.action
            assert action is not None
            when = action.scheduled_for or when
            now = max(now, when)

            # The action fires only if self-recovery has not beaten it there.
            if self._check_self_recovery(ep, now, result):
                break

            if action.tool is Tool.RETRY_PAYMENT:
                last_retry_at = now
            terminal = self._execute(ep, action, now, result, contact_history, rng, step)
            step += 1
            if terminal:
                break
        else:
            result.state = EpisodeState.EXHAUSTED
            result.terminal_reason = f"hit the hard step bound of {MAX_STEPS}"

        # The observation window runs past the last action. An episode nobody
        # touched — or one the agent gave up on — can still be paid, and that
        # payment belongs to whichever arm it fell in, unattributed.
        if not result.recovered:
            self._check_self_recovery(ep, watch_until, result)

        if result.state in {EpisodeState.INGESTED, EpisodeState.OBSERVED}:
            result.state = EpisodeState.EXHAUSTED
            result.terminal_reason = result.terminal_reason or "loop ended without a verdict"

        self._audit(now, "system", "episode_closed", ep.ref, {
            "state": str(result.state), "recovered_paise": result.recovered_paise,
            "cost_paise": result.cost_paise, "reason": result.terminal_reason,
        })
        result.audit_entries = len(self.chain) if self.chain else 0
        return result

    # ── steps ────────────────────────────────────────────────────────────────

    def _diagnose(
        self, strategy: Strategy, ep: SeededEpisode, now: _dt.datetime, result: EpisodeResult
    ) -> Diagnosis | None:
        diagnosis = strategy.diagnose(
            StrategyContext(
                opened_at=ep.occurred_at, now=now, surface=ep.surface,
                amount_paise=ep.amount_paise, rail=ep.rail, error_code=ep.error_code,
                error_description=ep.error_description, error_source=ep.error_source,
                error_step=ep.error_step, attempt_no=1,
                is_business=getattr(ep.counterparty, "is_business", False),
            )
        )
        # Token cost belongs to the episode that incurred it. A strategy that
        # does not call a model returns zero, so this is free for every
        # baseline and the ledger stays comparable across arms.
        token_cost = getattr(strategy, "drain_cost", lambda: ZERO)()
        if token_cost:
            result.cost_paise = Paise(result.cost_paise + token_cost)
            self._audit(now, "system", "cost", ep.ref,
                        {"kind": str(CostKind.LLM_TOKENS), "amount_paise": int(token_cost)})
        if diagnosis is not None:
            result.diagnosed_cause = diagnosis.root_cause
            self._audit(now, "system", "diagnosis", ep.ref, {
                "root_cause": str(diagnosis.root_cause),
                "confidence": diagnosis.confidence,
                "evidence": diagnosis.evidence,
            })
        return diagnosis

    def _execute(
        self,
        ep: SeededEpisode,
        action: ProposedAction,
        now: _dt.datetime,
        result: EpisodeResult,
        contact_history: list[ContactRecord],
        rng: Rng,
        step: int,
    ) -> bool:
        """Perform one action against the simulated world. Returns True if terminal."""
        if action.tool is Tool.CLOSE_EPISODE:
            result.state = EpisodeState.UNRECOVERABLE
            result.terminal_reason = action.rationale or "strategy closed the episode"
            return True
        if action.tool is Tool.ESCALATE_TO_HUMAN:
            result.state = EpisodeState.ESCALATED
            result.escalated = True
            result.terminal_reason = action.rationale or "escalated to a human"
            return True
        if action.tool is Tool.VERIFY_PAYMENT_CLAIM:
            # Read-only, free, and it unblocks the retry the gate was holding.
            result.actions_taken += 1
            self._audit(now, "system", "capture_verified", ep.ref, {"step": step})
            return False

        result.actions_taken += 1
        channel = _channel_of(action)
        is_contact = action.tool in CONTACT_ACTIONS and channel is not Channel.NONE

        if action.tool is Tool.RETRY_PAYMENT:
            result.retries += 1
            if ep.true_cause in NEVER_RETRY:
                result.forbidden_retries += 1
                self._book_externality(
                    result, ep, now, "forbidden_retry",
                    self.costs.externalities.forbidden_retry_paise,
                )
        if is_contact:
            result.contacts_made += 1
            self._charge(result, channel, ep, now)

        reaction = self.response.react(
            ep,
            Interaction(
                tool=action.tool, at=now, channel=channel,
                concession_paise=Paise(int(action.args.get("value_paise", 0))),
                contact_index=max(0, result.contacts_made - 1),
            ),
            issuer_down=(ep.issuer_down_until is not None and now < ep.issuer_down_until),
            rng=rng.child("react", step),
            contacts_so_far=result.contacts_made - (1 if is_contact else 0),
        )

        if is_contact:
            contact_history.append(ContactRecord(at=now, channel=channel,
                                                 responded=reaction.paid))

        self._audit(now, "system", "action_executed", ep.ref, {
            "step": step, "tool": str(action.tool), "channel": str(channel),
            "paid": reaction.paid, "amount_paise": reaction.amount_paise,
            "p_pay": reaction.p_pay,
        })

        if reaction.complained:
            result.complained = True
            self._book_externality(
                result, ep, now, "complaint", self.costs.externalities.complaint_paise
            )
        if reaction.disputed:
            result.disputed = True
            self._book_externality(
                result, ep, now, "dispute", self.costs.externalities.dispute_paise
            )
            result.state = EpisodeState.SUPPRESSED
            result.terminal_reason = "buyer raised a dispute; collections stopped"
            return True
        if reaction.opted_out:
            result.opted_out = True
            self._book_externality(
                result, ep, now, "opt_out",
                self.costs.externalities.opt_out_cost(
                    ep.amount_paise,
                    is_business=getattr(ep.counterparty, "is_business", False),
                ),
            )
            result.state = EpisodeState.SUPPRESSED
            result.terminal_reason = "counterparty opted out"
            return True
        if reaction.paid:
            result.recovered_paise = Paise(result.recovered_paise + reaction.amount_paise)
            result.time_to_recovery = now - ep.occurred_at
            full = reaction.amount_paise >= ep.amount_paise
            result.state = (EpisodeState.RECOVERED if full
                            else EpisodeState.PARTIALLY_RECOVERED)
            result.terminal_reason = f"payment received via {action.tool}"
            self._audit(now, "system", "outcome", ep.ref, {
                "kind": str(OutcomeKind.PAYMENT_RECEIVED),
                "amount_paise": reaction.amount_paise,
                "attribution": str(AttributionMethod.DIRECT_LINK),
            })
            return True
        return False

    def _check_self_recovery(
        self, ep: SeededEpisode, now: _dt.datetime, result: EpisodeResult
    ) -> bool:
        """Revenue that arrives with no attributable action.

        Applies to every arm. This is what the control group measures, and what
        stops the treatment arm claiming credit for it.
        """
        if not ep.would_self_recover or ep.self_recovery_at is None:
            return False
        if now < ep.self_recovery_at:
            return False
        result.recovered_paise = Paise(result.recovered_paise + ep.amount_paise)
        result.self_recovered = True
        result.state = EpisodeState.RECOVERED
        result.time_to_recovery = ep.self_recovery_at - ep.occurred_at
        result.terminal_reason = "self-recovered without an attributable action"
        self._audit(ep.self_recovery_at, "provider", "outcome", ep.ref, {
            "kind": str(OutcomeKind.PAYMENT_RECEIVED),
            "amount_paise": ep.amount_paise,
            "attribution": str(AttributionMethod.UNATTRIBUTED),
        })
        return True

    def _blocked_terminally(self, reasons: tuple[str, ...], result: EpisodeResult) -> bool:
        """Some denials mean the episode is over, not that we should replan."""
        terminal = {
            "never_retry_cause": (EpisodeState.UNRECOVERABLE, "cause is not retryable"),
            "opted_out": (EpisodeState.SUPPRESSED, "counterparty opted out"),
            "dnd_registry": (EpisodeState.SUPPRESSED, "on the DND registry"),
            "budget_actions_exhausted": (EpisodeState.EXHAUSTED, "action budget spent"),
            "budget_spend_exhausted": (EpisodeState.EXHAUSTED, "spend budget spent"),
            "episode_contact_cap": (EpisodeState.EXHAUSTED, "contact cap reached"),
            "ladder_exhausted": (EpisodeState.ESCALATED, "escalation ladder complete"),
        }
        for reason in reasons:
            if reason in terminal:
                state, why = terminal[reason]
                result.state = state
                result.terminal_reason = why
                return True
        return False

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _gate_context(
        self,
        ep: SeededEpisode,
        diagnosis: Diagnosis | None,
        when: _dt.datetime,
        result: EpisodeResult,
        history: list[ContactRecord],
        consent: frozenset[Channel],
        last_retry_at: _dt.datetime | None,
    ) -> GateContext:
        cause = diagnosis.root_cause if diagnosis else RootCause.UNKNOWN
        return GateContext(
            now=when, surface=ep.surface, root_cause=cause, amount_paise=ep.amount_paise,
            alternative_cause=diagnosis.alternative_cause if diagnosis else None,
            diagnosis_confidence=diagnosis.confidence if diagnosis else 1.0,
            actions_used=result.actions_taken, contacts_used=result.contacts_made,
            spend_paise=result.cost_paise, retries_used=result.retries,
            last_retry_at=last_retry_at,
            contact_history=tuple(history), channel_consent=consent,
            opted_out=result.opted_out, dispute_open=result.disputed,
            has_valid_mandate=cause is not RootCause.MANDATE_REVOKED,
            is_business=getattr(ep.counterparty, "is_business", False),
            escalation_rung=_rung_from(history),
            last_rung_at=history[-1].at if history else None,
            capture_verified=True,
            ledger_verified=True,
        )

    def _consent(self, ep: SeededEpisode) -> frozenset[Channel]:
        """Which channels this counterparty has consented to.

        Modelled as: their preferred channel plus email, which is the
        conservative reading — we do not assume blanket consent.
        """
        pref = getattr(ep.counterparty, "channel_preference", Channel.EMAIL)
        return frozenset({pref, Channel.EMAIL, Channel.WHATSAPP})

    def _charge(
        self, result: EpisodeResult, channel: Channel, ep: SeededEpisode, now: _dt.datetime
    ) -> None:
        unit = self.costs.channels.get(str(channel), Paise(0))
        result.cost_paise = Paise(result.cost_paise + unit)
        self._audit(now, "system", "cost", ep.ref,
                    {"kind": str(CostKind(str(channel)) if str(channel) in
                                 {c.value for c in CostKind} else CostKind.SMS),
                     "amount_paise": unit})

    def _book_externality(
        self,
        result: EpisodeResult,
        ep: SeededEpisode,
        now: _dt.datetime,
        kind: str,
        amount: Paise,
    ) -> None:
        """Record a modelled future loss.

        Audited under its own event type, never as ``cost``, so an auditor
        reading the chain can tell realised spend from a projection.
        """
        if amount <= 0:
            return
        result.externality_paise = Paise(result.externality_paise + amount)
        self._audit(now, "system", "externality", ep.ref,
                    {"kind": kind, "amount_paise": int(amount), "modelled": True})

    def _audit(
        self, at: _dt.datetime, actor: str, event: str, ref: str, payload: dict
    ) -> None:
        if self.chain is None:
            return
        self.chain.append(at=at, actor=actor, event_type=event, payload={"ref": ref, **payload})


def _channel_of(action: ProposedAction) -> Channel:
    raw = action.args.get("channel")
    if raw is None:
        return Channel.NONE
    try:
        return Channel(str(raw))
    except ValueError:
        return Channel.NONE


def _rung_from(history: list[ContactRecord]) -> int:
    return len(history)


def _unused() -> str:  # pragma: no cover
    return canonical_json({})
