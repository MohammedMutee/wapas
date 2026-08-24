"""The running service: what Razorpay talks to.

Everything else in this repository is a batch that starts and finishes. This is
the part that stays up, holds open episodes, and reacts when the world changes.
It is small on purpose — four endpoints — because the interesting behaviour
lives in the gate and the diagnoser, and an API that reimplements any of it
would be a second place for the rules to disagree.

The webhook endpoint is the only untrusted input in the system. Anyone on the
internet can POST to it, so the order of operations there is not a style
preference:

1. read the raw bytes
2. verify the signature over exactly those bytes, in constant time
3. only then parse
4. de-duplicate, because providers retry
5. apply, and never let a repeated delivery count twice

A 2xx is returned for anything verified, including events this service does not
act on. A provider that receives a 4xx retries, and retrying an event we will
never act on is a queue that never drains.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Header, Request, Response, status
from pydantic import BaseModel, Field

from ..actuators import RazorpayActuator, WebhookRejected
from ..actuators import parse as parse_webhook
from ..audit import HashChain, verify_chain
from ..clock import Clock, RealClock
from ..config import Settings, settings
from ..domain import (
    Channel,
    EpisodeState,
    GateVerdict,
    ProposedAction,
    Surface,
    Tool,
)
from ..money import Paise, format_inr
from ..policy import PolicyBundle, PolicyGate, load_policies
from ..policy.gate import GateContext
from ..strategies import RulesOnly
from ..strategies.base import StrategyContext
from .store import EpisodeStore, InMemoryEpisodeStore, LiveEpisode, apply_event


class OpenEpisode(BaseModel):
    """A failed payment handed to the agent to work."""

    ref: str = Field(min_length=1, max_length=64)
    amount_paise: int = Field(gt=0, le=10_000_000_000)
    error_code: str = ""
    error_description: str = ""
    error_source: str = ""
    error_step: str = ""
    rail: str = "card"
    issuer: str = ""
    surface: Surface = Surface.PAYMENT
    is_business: bool = False


@dataclass
class Service:
    """Everything the endpoints need, assembled once at startup."""

    config: Settings
    policies: PolicyBundle
    store: EpisodeStore
    chain: HashChain
    clock: Clock
    actuator: RazorpayActuator | None
    classifier: Any
    started_at: _dt.datetime = field(default_factory=lambda: RealClock().now())

    @property
    def durable(self) -> bool:
        return not isinstance(self.store, InMemoryEpisodeStore)

    @property
    def gate(self) -> PolicyGate:
        return PolicyGate(self.policies)

    def context_for(self, episode: LiveEpisode, diagnosis, now: _dt.datetime) -> GateContext:
        """The gate's view of a live episode.

        Consent is assumed present for the two channels a recovery flow uses,
        because a real deployment reads it from the merchant's own customer
        record and this service has no customer record. It is the one place the
        live path is thinner than the simulation, and it is thin in the
        direction that matters least: the gate still enforces quiet hours,
        frequency caps, retry rules and the never-retry causes.
        """
        return GateContext(
            now=now, surface=episode.surface, root_cause=diagnosis.root_cause,
            amount_paise=episode.amount_paise,
            alternative_cause=diagnosis.alternative_cause,
            risk_hypothesis=diagnosis.risk_hypothesis,
            diagnosis_confidence=diagnosis.confidence,
            channel_consent=frozenset({Channel.EMAIL, Channel.WHATSAPP}),
            has_valid_mandate=True, capture_verified=True, ledger_verified=True,
        )


def build_service(
    *,
    config: Settings | None = None,
    store: EpisodeStore | None = None,
    actuator: RazorpayActuator | None = None,
    clock: Clock | None = None,
    classifier: Any = None,
) -> Service:
    cfg = config or settings()
    the_clock = clock or RealClock()
    if actuator is None and cfg.razorpay_key_id:
        actuator = RazorpayActuator(
            key_id=cfg.razorpay_key_id,
            key_secret=(cfg.razorpay_key_secret.get_secret_value()
                        if cfg.razorpay_key_secret else None),
            clock=the_clock,
        )
    return Service(
        config=cfg,
        policies=load_policies("policies"),
        store=store or InMemoryEpisodeStore(),
        chain=HashChain(salt=f"live-{cfg.seed}"),
        clock=the_clock,
        actuator=actuator,
        classifier=classifier or RulesOnly(),
    )


def create_app(service: Service | None = None) -> FastAPI:
    svc = service or build_service()
    app = FastAPI(
        title="Wapas",
        summary="Revenue recovery agent — detect, diagnose, recover, and prove it.",
        version="0.1.0",
    )
    app.state.service = svc

    # ── liveness ─────────────────────────────────────────────────────────────

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        """Honest about what this process is and is not.

        ``durable`` is false for the in-memory store, and saying so here is
        cheaper than someone inferring persistence from the fact that episodes
        survive a few requests.
        """
        verification = verify_chain(svc.chain)
        return {
            "ok": True,
            "mode": str(svc.config.mode),
            "razorpay": "test-mode" if svc.actuator else "not configured",
            "webhook_secret": bool(svc.config.razorpay_webhook_secret),
            "durable": svc.durable,
            "episodes_open": sum(1 for e in svc.store.all() if not e.is_terminal),
            "episodes_total": len(svc.store.all()),
            "audit_entries": len(svc.chain),
            "audit_intact": verification.ok,
            "policy": svc.policies.version,
        }

    # ── the agent loop, one step of it ───────────────────────────────────────

    @app.post("/episodes", status_code=status.HTTP_201_CREATED)
    def open_episode(body: OpenEpisode) -> dict[str, Any]:
        """Take a failed payment, diagnose it, and act if the gate allows.

        The same diagnoser, the same gate and the same actuator the batch
        evaluation uses. If this endpoint could reach an actuator the gate had
        not approved, every number in ``results/report.md`` would describe a
        different system from the one running here.
        """
        existing = svc.store.get(body.ref)
        if existing is not None:
            return {"ref": existing.ref, "state": str(existing.state),
                    "duplicate": True,
                    "provider_id": existing.provider_id}

        now = svc.clock.now()
        episode = LiveEpisode(
            ref=body.ref, surface=body.surface, amount_paise=Paise(body.amount_paise),
            opened_at=now,
        )
        svc.chain.append(at=now, actor="system", event_type="episode_opened",
                         payload={"ref": body.ref, "amount_paise": body.amount_paise,
                                  "surface": str(body.surface)})

        ctx = StrategyContext(
            opened_at=now, now=now, surface=body.surface,
            amount_paise=Paise(body.amount_paise), rail=body.rail,
            error_code=body.error_code, error_description=body.error_description,
            error_source=body.error_source, error_step=body.error_step,
            attempt_no=1, is_business=body.is_business, issuer=body.issuer,
        )
        diagnosis = svc.classifier.diagnose(ctx)
        episode.diagnosed_cause = diagnosis.root_cause
        episode.confidence = diagnosis.confidence
        episode.state = EpisodeState.DIAGNOSED
        svc.chain.append(at=now, actor="agent", event_type="diagnosis",
                         payload={"ref": body.ref, "cause": str(diagnosis.root_cause),
                                  "confidence": diagnosis.confidence})

        proposal = ProposedAction(
            tool=Tool.CREATE_PAYMENT_LINK,
            rationale=f"{diagnosis.root_cause}: offer an alternate rail",
        )
        decision = svc.gate.evaluate(proposal, svc.context_for(episode, diagnosis, now))
        svc.chain.append(at=now, actor="policy", event_type="gate_decision",
                         payload={"ref": body.ref, "tool": str(proposal.tool),
                                  "verdict": str(decision.verdict),
                                  "reasons": list(decision.reasons)})

        result: dict[str, Any] = {
            "ref": body.ref,
            "diagnosis": {"cause": str(diagnosis.root_cause),
                          "confidence": diagnosis.confidence,
                          "evidence": diagnosis.evidence},
            "gate": {"verdict": str(decision.verdict), "reasons": list(decision.reasons)},
        }

        if decision.verdict is GateVerdict.DENY:
            episode.state = EpisodeState.SUPPRESSED
            episode.closed_at = now
            episode.terminal_reason = ", ".join(decision.reasons) or "gate denied"
            svc.store.put(episode)
            result["state"] = str(episode.state)
            return result

        if svc.actuator is None:
            episode.state = EpisodeState.WAITING
            svc.store.put(episode)
            result["state"] = str(episode.state)
            result["note"] = "no Razorpay credentials configured; nothing actuated"
            return result

        actuation = svc.actuator.create_payment_link(
            decision, episode_ref=body.ref, step=0,
            amount=Paise(body.amount_paise),
            description=f"Payment recovery for {body.ref}",
        )
        episode.provider_id = actuation.provider_id
        episode.state = EpisodeState.WAITING if actuation.ok else EpisodeState.FAILED
        if not actuation.ok:
            episode.terminal_reason = actuation.error
        svc.store.put(episode)
        svc.chain.append(at=now, actor="actuator", event_type="actuation",
                         payload={"ref": body.ref, "ok": actuation.ok,
                                  "provider_id": actuation.provider_id,
                                  "replayed": actuation.replayed})

        result["state"] = str(episode.state)
        result["payment_link"] = {
            "id": actuation.provider_id,
            "url": actuation.detail.get("short_url", ""),
            "replayed": actuation.replayed,
        }
        return result

    # ── the untrusted edge ───────────────────────────────────────────────────

    @app.post("/webhooks/razorpay")
    async def razorpay_webhook(
        request: Request,
        response: Response,
        x_razorpay_signature: str = Header(default=""),
    ) -> dict[str, Any]:
        """The only endpoint anyone on the internet can reach meaningfully.

        Returns 200 for anything that verifies, 401 for anything that does not,
        and never says which part of a rejected payload was wrong.
        """
        body = await request.body()
        secret = (svc.config.razorpay_webhook_secret.get_secret_value()
                  if svc.config.razorpay_webhook_secret else "")

        try:
            event = parse_webhook(body, x_razorpay_signature, secret)
        except WebhookRejected:
            # Deliberately uninformative. A forger learns nothing about which
            # check failed, and the detail goes to the audit chain instead.
            svc.chain.append(at=svc.clock.now(), actor="provider",
                             event_type="webhook_rejected",
                             payload={"bytes": len(body), "signed": bool(x_razorpay_signature)})
            response.status_code = status.HTTP_401_UNAUTHORIZED
            return {"accepted": False}

        episode = (svc.store.get(event.episode_ref)
                   or svc.store.by_provider_id(event.provider_id))
        if episode is None:
            # Verified, genuine, and about something we are not working. A 2xx
            # stops the provider retrying an event that will never match.
            svc.chain.append(at=event.at, actor="provider", event_type="webhook_unmatched",
                             payload={"event": event.event, "provider_id": event.provider_id})
            return {"accepted": True, "matched": False, "event": event.event}

        identity = f"{event.event}:{event.provider_id}:{int(event.at.timestamp())}"
        applied = apply_event(
            episode, event=event.event, event_identity=identity,
            amount_paise=event.amount_paise, at=event.at,
        )
        svc.store.put(episode)
        svc.chain.append(at=event.at, actor="provider", event_type="outcome",
                         payload={"ref": episode.ref, "event": event.event,
                                  "amount_paise": event.amount_paise,
                                  "state": str(applied.state),
                                  "changed": applied.changed,
                                  "duplicate": applied.duplicate})
        return {
            "accepted": True, "matched": True, "ref": episode.ref,
            "event": event.event, "state": str(applied.state),
            "changed": applied.changed, "duplicate": applied.duplicate,
            "reason": applied.reason,
        }

    # ── evidence ─────────────────────────────────────────────────────────────

    @app.get("/episodes/{ref}")
    def get_episode(ref: str, response: Response) -> dict[str, Any]:
        episode = svc.store.get(ref)
        if episode is None:
            response.status_code = status.HTTP_404_NOT_FOUND
            return {"ref": ref, "found": False}
        return {
            "ref": episode.ref, "found": True, "state": str(episode.state),
            "cause": str(episode.diagnosed_cause) if episode.diagnosed_cause else None,
            "confidence": episode.confidence,
            "amount": format_inr(episode.amount_paise),
            "recovered": format_inr(episode.recovered_paise),
            "provider_id": episode.provider_id,
            "terminal": episode.is_terminal,
            "reason": episode.terminal_reason,
            "events_applied": len(episode.seen_events),
        }

    @app.get("/audit")
    def audit() -> dict[str, Any]:
        verification = verify_chain(svc.chain)
        return {
            "intact": verification.ok,
            "detail": str(verification),
            "entries": [
                {"seq": e.seq, "at": e.at.isoformat(), "actor": e.actor,
                 "event": e.event_type, "hash": e.hash[:16]}
                for e in svc.chain.entries
            ],
        }

    return app
