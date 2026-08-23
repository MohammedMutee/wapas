"""The diagnosis step: failure signal in, validated root cause out.

The contract with everything downstream is narrow on purpose.

* It returns a :class:`~wapas.domain.Diagnosis` or it returns ``None``. It
  never returns a guess dressed as an answer.
* On any failure — transport, validation, budget — it degrades to the rules
  classifier and records why. A model being unavailable must not stop a
  merchant recovering money.
* Every call is priced into the episode ledger, including the ones that failed
  and were retried. The headline metric is *net* recovery, so a model that
  needs three attempts is a model that costs three attempts.
* Every call is audited by prompt digest, so any diagnosis in the report can be
  traced back to the exact prompt that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..domain import DISPOSITIONS, UNRECOVERABLE, Diagnosis
from ..llm.base import LLMProvider, StructuredMode, prompt_digest
from ..llm.costs import CostBook, cost_paise, cost_usd
from ..llm.structured import ask_structured
from ..money import ZERO, Paise
from ..strategies.base import StrategyContext
from ..strategies.rules import RulesOnly
from .prompt import SYSTEM, build_user_prompt
from .schema import DiagnosisResponse


@dataclass
class DiagnoserStats:
    """What the run cost and how often the model was actually usable."""

    calls: int = 0
    cache_hits: int = 0
    failures: int = 0
    budget_stops: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    spend_paise: int = 0
    spend_usd: Decimal = Decimal(0)
    attempts: int = 0
    failure_reasons: list[str] = field(default_factory=list)

    @property
    def fallback_rate(self) -> float:
        total = self.calls + self.cache_hits
        return self.failures / total if total else 0.0


class LLMDiagnoser:
    """Classifies an episode with a model, and degrades to rules when it cannot.

    ``budget_usd`` is a hard ceiling, not a warning. Once the run has spent it
    the diagnoser stops calling out entirely and reports how many episodes were
    affected, so a runaway loop cannot quietly turn a free tier into a bill.
    """

    name = "llm_diagnoser"

    def __init__(
        self,
        provider: LLMProvider,
        *,
        model: str,
        costs: CostBook,
        cache=None,
        budget_usd: float = 15.0,
        max_tokens: int = 1400,
        fallback_models: tuple[str, ...] = (),
        history=None,
        fleet=None,
        neighbour_threshold: float = 0.60,
    ) -> None:
        self.provider = provider
        self.model = model
        self.history = history
        """The merchant's resolved past. Used three ways, in this order.

        A wording resolved consistently before is answered **without calling
        the model at all** — for a fixed vocabulary a lookup is optimal, it is
        free, and second-guessing it with a language model would be slower,
        costlier and worse. That short-circuit also means the model is only
        asked about episodes history cannot already answer, which is exactly
        where its value has to be demonstrated.

        For the rest, base rates and near-duplicate wordings go into the
        prompt as evidence.
        """
        self.fleet = fleet
        """Live failure traffic across episodes. Evidence about this payment
        that this payment's own text does not contain."""
        self.neighbour_threshold = neighbour_threshold
        """Retrieved exemplars below this similarity are withheld.

        Set high enough that only near-duplicate wordings qualify — a changed
        preposition, different casing — which is a real thing acquirers do and
        which ``exact`` would miss.

        It is deliberately not doing few-shot retrieval, because that was tried
        and measured. Character-overlap retrieval at low similarity is not
        merely useless on unseen wordings but *misleading*: "Recurring debit
        bounced at destination bank" retrieves `mandate_revoked` at 0.27,
        confidently wrong. Semantic retrieval with `nv-embedqa-e5-v5` does
        better — 8 of 13 novel wordings matched to the right cause — but the
        model answers 12 of 13 of those correctly from the text alone, so
        retrieval would have been a second network dependency, a second index
        to keep warm, and a source of confident wrong exemplars, in exchange
        for nothing. See DECISIONS.md D32.
        """
        self.fallback_models = fallback_models
        """Models to try, in order, when the primary will not answer.

        The NVIDIA developer tier is free and its models go through periods of
        being unusable — measured on 2026-08-23, ``gpt-oss-120b`` took 67
        seconds to return an empty completion for a one-line prompt. Falling
        through to a second model is a better answer than falling back to
        keywords, and which model actually answered is recorded on the
        diagnosis and counted in the stats."""
        self.costs = costs
        self.cache = cache
        self.budget_usd = Decimal(str(budget_usd))
        self.max_tokens = max_tokens
        self.stats = DiagnoserStats()
        self.by_model: dict[str, int] = {}
        self.history_hits = 0
        self.no_signal_hits = 0
        self.rules = RulesOnly(history=history, fleet=fleet)
        self._last_cost: Paise = ZERO

    # ── the call ─────────────────────────────────────────────────────────────

    def prompt_for(self, ctx: StrategyContext) -> tuple[str, str]:
        """The user prompt for an episode and its cache digest.

        Exposed so the cache warmer can enumerate and deduplicate prompts
        without reimplementing how they are built — a warmer that computed a
        different digest would fill a cache nothing ever reads.
        """
        prior = neighbours = None
        if self.history is not None:
            prior = self.history.prior(
                surface=ctx.surface, rail=ctx.rail, step=ctx.error_step,
                source=ctx.error_source, code=ctx.error_code,
            )
            neighbours = [
                pair for pair in self.history.neighbours(
                    ctx.error_description, k=3, surface=ctx.surface
                )
                if pair[1] >= self.neighbour_threshold
            ] or None
        outage = False
        if self.fleet is not None and ctx.issuer:
            signal = self.fleet.signal_at(ctx.issuer, ctx.now)
            outage = bool(signal and signal.spiking)
        user = build_user_prompt(
            surface=ctx.surface, rail=ctx.rail, error_code=ctx.error_code,
            error_description=ctx.error_description, error_source=ctx.error_source,
            error_step=ctx.error_step, amount_paise=ctx.amount_paise,
            is_business=ctx.is_business, prior=prior, neighbours=neighbours,
            issuer_spiking=outage,
        )
        mode = self.provider.profile(self.model).best_mode()
        return user, prompt_digest(SYSTEM, user, self.model, mode)

    def diagnose(self, ctx: StrategyContext) -> Diagnosis:
        self._last_cost = ZERO

        # History first. A wording resolved consistently before is known, and
        # asking a model to reconsider it would be slower, costlier and worse.
        if self.history is not None:
            known = self.history.exact(ctx.error_description)
            if known is not None:
                cause, purity = known
                self.history_hits += 1
                return Diagnosis(
                    root_cause=cause, confidence=min(0.97, purity),
                    evidence=[f"this exact wording resolved to {cause} in history"],
                    recoverable=cause not in UNRECOVERABLE,
                    recommended_horizon_hours=DISPOSITIONS[cause].default_horizon_hours or 24,
                    notes="resolved-history lookup; no model call needed",
                )

        # Then: text that says nothing at all.
        #
        # For a wording history has seen many times under many causes, there is
        # no reading to be done. What is left is an outage check and, failing
        # that, the most likely cause for this context — both deterministic,
        # both optimal, and measurably better than asking a model. On identical
        # episodes the deterministic path scores 50.7% on this text and the
        # model 45.7%, because the model is being asked to reproduce an argmax
        # over base rates and there are better ways to compute an argmax.
        #
        # This is the same principle as the exact-history lookup one branch up.
        # The model is for text that can be read. Routing everything through it
        # because it is the interesting component would be worse on the metric
        # and worse on the bill.
        if (self.history is not None
                and self.history.known_ambiguous(ctx.error_description)):
            self.no_signal_hits += 1
            return self.rules.diagnose(ctx)

        user, digest = self.prompt_for(ctx)

        cached = self.cache.get(digest) if self.cache is not None else None
        if cached is not None:
            self.stats.cache_hits += 1
            return self._to_domain(DiagnosisResponse.model_validate(cached), digest,
                                   cached=True, served_by=self.model, ctx=ctx)

        if self.stats.spend_usd >= self.budget_usd:
            self.stats.budget_stops += 1
            return self._degraded(ctx, "llm budget exhausted for this run")

        parsed = response = None
        served_by = self.model
        for candidate in (self.model, *self.fallback_models):
            try:
                parsed, response = ask_structured(
                    self.provider, model=candidate, system=SYSTEM, user=user,
                    schema_model=DiagnosisResponse, max_tokens=self.max_tokens,
                )
                served_by = candidate
                break
            except Exception as exc:
                # Deliberately broad. Any failure to obtain a *validated*
                # diagnosis is the same event downstream: try the next model,
                # and if there is none, fall back to rules and keep recovering
                # money. Narrowing this would turn a provider outage into a
                # failed evaluation run.
                self.stats.failure_reasons.append(
                    f"{candidate}: {type(exc).__name__}: {exc}"
                )
        if parsed is None or response is None:
            self.stats.failures += 1
            return self._degraded(ctx, "every model refused to return a valid diagnosis")

        self.by_model[served_by] = self.by_model.get(served_by, 0) + 1
        self.stats.calls += 1
        self.stats.attempts += response.attempts
        self.stats.input_tokens += response.usage.input_tokens
        self.stats.output_tokens += response.usage.output_tokens
        charge = cost_paise(response.usage, served_by, self.costs)
        self.stats.spend_paise += int(charge)
        self.stats.spend_usd += cost_usd(
            response.usage, self.costs.model_rate(served_by)
        )
        self._last_cost = charge

        if self.cache is not None:
            self.cache.put(digest, parsed.model_dump(mode="json"))
        return self._to_domain(parsed, digest, cached=False, served_by=served_by, ctx=ctx)

    def drain_cost(self) -> Paise:
        """Token cost of the most recent call, for the episode ledger.

        Drained rather than read so it cannot be booked twice.
        """
        cost, self._last_cost = self._last_cost, ZERO
        return cost

    # ── conversion and fallback ──────────────────────────────────────────────

    def _to_domain(
        self, parsed: DiagnosisResponse, digest: str, *, cached: bool,
        served_by: str = "", ctx: StrategyContext | None = None,
    ) -> Diagnosis:
        # ``Diagnosis.evidence`` caps at five. The prompt digest is the replay
        # key and must always survive, and the runner-up is the model's own
        # statement of ambiguity, so the model's quotes are what gets trimmed.
        alternative = parsed.alternative_cause
        confidence = parsed.confidence

        # A wording history has seen many times under many causes cannot support
        # a confident answer, whatever the model says about it. The
        # signal_quality validator (D24) assumes the model grades the text
        # honestly, and mostly it does; when it does not — "Transaction
        # declined" graded `specific` and answered at 0.85 — there is a
        # deterministic check available that does not rely on self-report.
        if (self.history is not None and ctx is not None
                and self.history.known_ambiguous(ctx.error_description)):
            confidence = min(confidence, 0.5)

        risk = None
        if confidence < 0.75 and self.history is not None and ctx is not None:
            distribution, _ = self.history.prior(
                surface=ctx.surface, rail=ctx.rail, step=ctx.error_step,
                source=ctx.error_source, code=ctx.error_code,
            )
            # Computed whether or not the model named a runner-up of its own.
            # These answer different questions and only one of them decides
            # whether a retry is safe.
            risk = self.history.riskiest_alternative(distribution)

        quotes = list(parsed.evidence)
        tail = [f"prompt {digest[:12]}"]
        if alternative is not None:
            tail.insert(0, f"runner-up considered: {alternative}")
        if risk is not None and risk != alternative:
            tail.insert(0, f"not ruled out, and never retryable: {risk}")
        evidence = quotes[: max(0, 5 - len(tail))] + tail
        return Diagnosis(
            root_cause=parsed.root_cause,
            confidence=confidence,
            alternative_cause=alternative,
            risk_hypothesis=risk,
            evidence=evidence,
            recoverable=parsed.recoverable,
            recommended_horizon_hours=parsed.recommended_horizon_hours,
            notes=(parsed.notes or f"{served_by}{' (cached)' if cached else ''}")[:280],
        )

    def _degraded(self, ctx: StrategyContext, why: str) -> Diagnosis:
        fallback = self.rules.diagnose(ctx)
        return Diagnosis(
            root_cause=fallback.root_cause,
            # A fallback is less trustworthy than the rules classifier standing
            # on its own, because we wanted a model opinion and did not get one.
            confidence=min(fallback.confidence, 0.6),
            evidence=[*fallback.evidence, why],
            recoverable=fallback.recoverable,
            recommended_horizon_hours=fallback.recommended_horizon_hours,
            notes=f"degraded to rules: {why}",
        )


def default_mode(provider: LLMProvider, model: str) -> StructuredMode:
    return provider.profile(model).best_mode()
