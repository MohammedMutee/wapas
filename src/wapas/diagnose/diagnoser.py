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

from ..domain import Diagnosis
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
    ) -> None:
        self.provider = provider
        self.model = model
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
        self.rules = RulesOnly()
        self._last_cost: Paise = ZERO

    # ── the call ─────────────────────────────────────────────────────────────

    def prompt_for(self, ctx: StrategyContext) -> tuple[str, str]:
        """The user prompt for an episode and its cache digest.

        Exposed so the cache warmer can enumerate and deduplicate prompts
        without reimplementing how they are built — a warmer that computed a
        different digest would fill a cache nothing ever reads.
        """
        user = build_user_prompt(
            surface=ctx.surface, rail=ctx.rail, error_code=ctx.error_code,
            error_description=ctx.error_description, error_source=ctx.error_source,
            error_step=ctx.error_step, amount_paise=ctx.amount_paise,
            is_business=ctx.is_business,
        )
        mode = self.provider.profile(self.model).best_mode()
        return user, prompt_digest(SYSTEM, user, self.model, mode)

    def diagnose(self, ctx: StrategyContext) -> Diagnosis:
        self._last_cost = ZERO
        user, digest = self.prompt_for(ctx)

        cached = self.cache.get(digest) if self.cache is not None else None
        if cached is not None:
            self.stats.cache_hits += 1
            return self._to_domain(DiagnosisResponse.model_validate(cached), digest,
                                   cached=True, served_by=self.model)

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
        return self._to_domain(parsed, digest, cached=False, served_by=served_by)

    def drain_cost(self) -> Paise:
        """Token cost of the most recent call, for the episode ledger.

        Drained rather than read so it cannot be booked twice.
        """
        cost, self._last_cost = self._last_cost, ZERO
        return cost

    # ── conversion and fallback ──────────────────────────────────────────────

    def _to_domain(
        self, parsed: DiagnosisResponse, digest: str, *, cached: bool,
        served_by: str = "",
    ) -> Diagnosis:
        # ``Diagnosis.evidence`` caps at five. The prompt digest is the replay
        # key and must always survive, and the runner-up is the model's own
        # statement of ambiguity, so the model's quotes are what gets trimmed.
        quotes = list(parsed.evidence)
        tail = [f"prompt {digest[:12]}"]
        if parsed.alternative_cause is not None:
            tail.insert(0, f"runner-up considered: {parsed.alternative_cause}")
        evidence = quotes[: max(0, 5 - len(tail))] + tail
        return Diagnosis(
            root_cause=parsed.root_cause,
            confidence=parsed.confidence,
            alternative_cause=parsed.alternative_cause,
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
