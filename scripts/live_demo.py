"""One real episode, end to end, against Razorpay test mode.

Everything else in this repository runs in simulation, which is what makes the
numbers measurable and is also the first thing a sceptic will push on. This
script exists to answer that push: the same diagnosis, the same policy gate,
the same audit chain, and a payment link that genuinely exists in a Razorpay
account you can open in another tab.

    python scripts/live_demo.py            # creates one real test-mode link
    python scripts/live_demo.py --dry-run  # every step except the network

It deliberately shows the gate **refusing** something as well as allowing it.
An agent that only ever succeeds on camera tells you nothing about what it does
at 3 a.m.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sim import build_population, load_params
from wapas.actuators import RazorpayActuator, parse, sign
from wapas.audit import HashChain, verify_chain
from wapas.clock import IST, RealClock
from wapas.config import settings
from wapas.diagnose.fleet import FleetView
from wapas.diagnose.history import build_history
from wapas.domain import Channel, GateVerdict, ProposedAction, RootCause, Tool
from wapas.money import format_inr
from wapas.policy import PolicyGate, load_policies
from wapas.policy.gate import GateContext
from wapas.strategies import RulesOnly
from wapas.strategies.base import StrategyContext

RULE = "─" * 74


def head(title: str) -> None:
    print(f"\n{RULE}\n  {title}\n{RULE}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seed", type=int, default=20260901)
    ap.add_argument("--keep", action="store_true", help="do not cancel the link afterwards")
    args = ap.parse_args()

    cfg = settings()
    params = load_params()
    policies = load_policies("policies")
    start = _dt.datetime(2026, 6, 1, tzinfo=IST)
    chain = HashChain(salt="live-demo")

    # A real episode from the seeded world: a card the customer abandoned at
    # the bank page, which is precisely the case a retry cannot fix.
    population = build_population(params, run_seed=args.seed, start=start)
    episode = next(
        e for e in population.episodes
        if e.true_cause is RootCause.AUTHENTICATION_FAILED and e.amount_paise > 100_000
    )

    head("1. THE FAILURE, as the gateway reported it")
    print(f"  episode      {episode.ref}")
    print(f"  amount       {format_inr(episode.amount_paise)}")
    print(f"  rail         {episode.rail} on {episode.issuer or 'unknown bank'}")
    print(f"  error        {episode.error_code}: {episode.error_description}")
    print(f"  (ground truth, never shown to the agent: {episode.true_cause})")

    head("2. DIAGNOSIS")
    history = build_history(params, seed=770777, start=start)
    fleet = FleetView.from_episodes([e for e in population.episodes if e.issuer])
    ctx = StrategyContext(
        opened_at=episode.occurred_at, now=episode.occurred_at, surface=episode.surface,
        amount_paise=episode.amount_paise, rail=episode.rail,
        error_code=episode.error_code, error_description=episode.error_description,
        error_source=episode.error_source, error_step=episode.error_step,
        attempt_no=1, is_business=False, issuer=episode.issuer,
    )
    diagnosis = RulesOnly(history=history, fleet=fleet).diagnose(ctx)
    print(f"  cause        {diagnosis.root_cause}  (confidence {diagnosis.confidence:.2f})")
    for line in diagnosis.evidence:
        print(f"  evidence     {line}")
    print(f"  correct?     {'yes' if diagnosis.root_cause is episode.true_cause else 'NO'}")
    chain.append(at=episode.occurred_at, actor="agent", event_type="diagnosis",
                 payload={"ref": episode.ref, "cause": str(diagnosis.root_cause)})

    gate = PolicyGate(policies)
    base_ctx = dict(
        surface=episode.surface, root_cause=diagnosis.root_cause,
        amount_paise=episode.amount_paise, has_valid_mandate=True,
        capture_verified=True, ledger_verified=True,
        channel_consent=frozenset({Channel.EMAIL, Channel.WHATSAPP}),
    )

    head("3. THE GATE REFUSING THINGS  (the part that matters)")
    refusals = [
        ("re-present the same 3DS flow the customer just abandoned",
         ProposedAction(tool=Tool.RETRY_PAYMENT, rationale="demo"),
         dict(root_cause=RootCause.RISK_DECLINED)),
        ("message them at 03:30",
         ProposedAction(tool=Tool.SEND_MESSAGE,
                        args={"channel": "whatsapp", "rung": 1}, rationale="demo"),
         dict(now=episode.occurred_at.replace(hour=3, minute=30))),
        ("contact them after they opted out",
         ProposedAction(tool=Tool.SEND_MESSAGE,
                        args={"channel": "whatsapp", "rung": 1}, rationale="demo"),
         dict(opted_out=True)),
    ]
    for label, action, extra in refusals:
        merged = {**base_ctx, "now": episode.occurred_at, **extra}
        decision = gate.evaluate(action, GateContext(**merged))
        mark = "REFUSED " if decision.verdict is GateVerdict.DENY else str(decision.verdict).upper()
        print(f"  {mark:9} {label}")
        print(f"            -> {', '.join(decision.reasons) or 'allowed'}")
        chain.append(at=episode.occurred_at, actor="policy", event_type="gate_decision",
                     payload={"ref": episode.ref, "verdict": str(decision.verdict),
                              "reasons": list(decision.reasons)})

    head("4. THE ACTION IT DOES TAKE")
    proposal = ProposedAction(tool=Tool.CREATE_PAYMENT_LINK,
                              rationale="authentication drop-off: switch rails")
    decision = gate.evaluate(proposal, GateContext(now=episode.occurred_at, **base_ctx))
    print(f"  gate         {str(decision.verdict).upper()}")
    if decision.verdict is GateVerdict.DENY:
        print("  the gate refused the recovery action; nothing to actuate.")
        return 1

    actuator = RazorpayActuator(
        key_id=cfg.razorpay_key_id,
        key_secret=cfg.razorpay_key_secret.get_secret_value() if cfg.razorpay_key_secret else None,
        clock=RealClock(), dry_run=args.dry_run, audit=chain,
    )
    result = actuator.create_payment_link(
        decision, episode_ref=episode.ref, step=0, amount=episode.amount_paise,
        description=f"Recovery for {episode.ref}",
    )
    print(f"  actuator     {'ok' if result.ok else 'FAILED: ' + result.error}")
    print(f"  provider id  {result.provider_id}")
    if result.detail.get("short_url"):
        print(f"  LIVE LINK    {result.detail['short_url']}")
    print(f"  idempotency  {result.idempotency_key}")

    print("\n  calling it a second time, as a crashed worker would:")
    again = actuator.create_payment_link(
        decision, episode_ref=episode.ref, step=0, amount=episode.amount_paise,
        description=f"Recovery for {episode.ref}",
    )
    print(f"  -> {'replayed' if again.replayed else 'reconciled' if again.reconciled else 'NEW LINK'}"
          f", same id {again.provider_id == result.provider_id}, "
          f"network calls so far: {actuator.calls_made}")

    head("5. THE CUSTOMER PAYS  (webhook, verified before it is believed)")
    secret = (cfg.razorpay_webhook_secret.get_secret_value()
              if cfg.razorpay_webhook_secret else "demo-secret")
    body = json.dumps({
        "event": "payment_link.paid", "created_at": int(RealClock().now().timestamp()),
        "payload": {"payment_link": {"entity": {
            "id": result.provider_id, "amount": int(episode.amount_paise),
            "notes": {"episode_ref": episode.ref}}}},
    }).encode()

    try:
        parse(body, "deadbeef" * 8, secret)
        print("  FORGED EVENT WAS ACCEPTED — this is a bug")
    except Exception as exc:
        print(f"  forged event REJECTED  -> {exc}")

    event = parse(body, sign(body, secret), secret)
    print(f"  genuine event accepted -> {event.event} for {event.episode_ref}, "
          f"{format_inr(event.amount_paise)}")
    chain.append(at=event.at, actor="provider", event_type="outcome",
                 payload={"ref": event.episode_ref, "kind": event.event,
                          "amount_paise": event.amount_paise})

    head("6. THE EVIDENCE")
    print(f"  {verify_chain(chain)}")
    print("  every step above is in the chain, in order, and any edit breaks it:")
    for entry in chain.entries:
        print(f"    #{entry.seq}  {entry.event_type:16} {entry.hash[:16]}...")

    tampered = HashChain(salt="live-demo")
    tampered.entries = list(chain.entries)
    import dataclasses
    tampered.entries[1] = dataclasses.replace(
        tampered.entries[1], payload={"ref": episode.ref, "cause": "insufficient_funds"}
    )
    print(f"\n  after editing entry #1's diagnosis: {verify_chain(tampered)}")

    if result.provider_id and not args.dry_run and not args.keep:
        actuator.cancel_payment_link(result.provider_id)
        print(f"\n  (demo link {result.provider_id} cancelled)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
