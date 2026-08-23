"""Resolved history: what the merchant already knows.

Until now the classifier met every failure as if for the first time. That is
not what a deployed system looks like. A merchant who has been taking payments
for six months has thousands of episodes whose outcome is now known — the
customer paid, the card was replaced, the mandate was re-signed — and knows
both what "Issuer response 51" means on their traffic and how often each cause
actually occurs. Withholding that made the task harder than the real one and
made every accuracy number lower than it should have been.

Three things history provides, in increasing order of how much they matter.

**Exact recall.** A wording seen before with a consistent resolution is simply
known. For a fixed vocabulary of error strings a lookup table is *optimal*, and
no model can beat it — which is the honest reason this class also serves the
keyword baseline. If the vocabulary never changed, the right answer would be a
dictionary and this project would not need an LLM.

**Base rates.** When the text says nothing, the cause distribution conditioned
on surface, rail, step and source is the best available evidence. The Bayes
ceiling on uninformative episodes is about 46%, against the 18% you get by
always naming the single most common cause, so most of that is in the
conditioning rather than the marginal.

**Neighbours.** Wordings that are *close* to something resolved before. This is
where it stops being a lookup: the vocabulary does change, and the question a
recovery system actually faces is what to do the first time an acquirer phrases
a decline differently.

History never contains the episode being classified — it is a separate
population from a separate seed — and it never contains a novel phrasing, so
roughly a quarter of the evaluation's informative episodes carry text this
class has never seen.
"""

from __future__ import annotations

import datetime as _dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..domain import RootCause, Surface


def _grams(text: str, n: int = 4) -> set[str]:
    text = " " + " ".join(text.lower().split()) + " "
    return {text[i : i + n] for i in range(max(1, len(text) - n + 1))}


@dataclass(frozen=True, slots=True)
class Exemplar:
    """One resolved episode, reduced to what a classifier may see plus the answer."""

    description: str
    code: str
    source: str
    step: str
    surface: Surface
    rail: str
    cause: RootCause


@dataclass
class ResolvedHistory:
    exemplars: list[Exemplar] = field(default_factory=list)
    _by_text: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    _by_context: dict[tuple, Counter] = field(default_factory=lambda: defaultdict(Counter))
    _global: Counter = field(default_factory=Counter)
    _grams: dict[str, set[str]] = field(default_factory=dict)

    # ── construction ─────────────────────────────────────────────────────────

    @classmethod
    def from_episodes(cls, episodes) -> ResolvedHistory:
        history = cls()
        for ep in episodes:
            history.add(Exemplar(
                description=ep.error_description, code=ep.error_code,
                source=ep.error_source, step=ep.error_step, surface=ep.surface,
                rail=ep.rail, cause=ep.true_cause,
            ))
        return history

    def add(self, ex: Exemplar) -> None:
        self.exemplars.append(ex)
        self._by_text[ex.description.lower().strip()][ex.cause] += 1
        self._global[ex.cause] += 1
        for key in self._context_keys(ex.surface, ex.rail, ex.step, ex.source, ex.code):
            self._by_context[key][ex.cause] += 1
        if ex.description and ex.description not in self._grams:
            self._grams[ex.description] = _grams(ex.description)

    @staticmethod
    def _context_keys(surface, rail, step, source, code) -> list[tuple]:
        """Most specific first. Backoff walks this list in order."""
        return [
            ("full", str(surface), rail, step, source, code),
            ("no_code", str(surface), rail, step, source),
            ("surface_rail_step", str(surface), rail, step),
            ("surface_rail", str(surface), rail),
            ("surface", str(surface)),
        ]

    # ── what it can answer ───────────────────────────────────────────────────

    def exact(self, description: str, *, min_purity: float = 0.8) -> tuple[RootCause, float] | None:
        """A wording resolved consistently before.

        ``min_purity`` guards the ambiguous ones. "Payment failed" appears in
        history under half a dozen causes, and returning its plurality winner
        as if it were knowledge is the guessing this project keeps trying to
        stop rewarding.
        """
        counts = self._by_text.get(description.lower().strip())
        if not counts:
            return None
        cause, n = counts.most_common(1)[0]
        purity = n / sum(counts.values())
        return (cause, purity) if purity >= min_purity else None

    def prior(self, *, surface, rail: str, step: str, source: str,
              code: str, min_support: int = 25) -> tuple[list[tuple[RootCause, float]], str]:
        """Cause distribution given observable context, with backoff.

        Returns the distribution and the name of the level that supplied it, so
        a prompt can tell the model how specific its evidence is rather than
        presenting a global base rate as if it were conditional.
        """
        for key in self._context_keys(surface, rail, step, source, code):
            counts = self._by_context.get(key)
            if counts and sum(counts.values()) >= min_support:
                total = sum(counts.values())
                return ([(c, n / total) for c, n in counts.most_common()], key[0])
        total = sum(self._global.values()) or 1
        return ([(c, n / total) for c, n in self._global.most_common()], "global")

    def neighbours(self, description: str, k: int = 4,
                   min_similarity: float = 0.25,
                   surface: Surface | None = None) -> list[tuple[Exemplar, float]]:
        """Resolved wordings closest to this one, by character 4-gram overlap.

        Deliberately not an embedding model. The comparison set is a few dozen
        distinct strings, the similarity that matters is largely lexical
        ("NOT SUFFICIENT FUNDS" against "insufficient balance"), and a second
        model in the loop would be a second thing to explain and a second thing
        to go wrong.
        """
        if not description.strip():
            return []

        # If history has seen this exact wording and found it ambiguous, the
        # wording carries no signal and every lexical match on it is spurious.
        # Retrieval has nothing to offer; the base rates do the work.
        key = description.lower().strip()
        if key in self._by_text and self.exact(description) is None:
            return []

        target = _grams(description)
        seen: dict[str, tuple[Exemplar, float]] = {}
        for ex in self.exemplars:
            if not ex.description or ex.description in seen:
                continue
            # A mandate failure is not explained by a card-checkout string.
            # Surfaces have different vocabularies and different causes
            # available to them, so a cross-surface exemplar is noise dressed
            # as evidence.
            if surface is not None and ex.surface is not surface:
                continue
            # Only offer a neighbour whose own resolution history is consistent.
            # Without this the uninformative strings come back as exemplars:
            # "Payment failed" matches itself at similarity 1.00 and carries
            # whichever cause happened to be most common underneath it, which
            # is a confident label on a string that means nothing. An exemplar
            # is a claim that this wording indicates that cause, and history
            # is only entitled to make that claim where it holds.
            if self.exact(ex.description) is None:
                continue
            other = self._grams.get(ex.description)
            if not other:
                continue
            score = len(target & other) / max(1, len(target | other))
            if score >= min_similarity:
                seen[ex.description] = (ex, score)
        return sorted(seen.values(), key=lambda pair: -pair[1])[:k]

    def known_ambiguous(self, description: str) -> bool:
        """True when history has seen this wording and found it meaningless.

        A deterministic ceiling on confidence that does not depend on the
        classifier grading its own evidence honestly. "Transaction declined"
        has appeared hundreds of times under six different causes; nobody is
        85% sure what it means, however sure they sound.
        """
        key = description.lower().strip()
        return key in self._by_text and self.exact(description) is None

    def riskiest_alternative(
        self, distribution: list[tuple[RootCause, float]], *, min_mass: float = 0.15
    ) -> RootCause | None:
        """The most likely never-retryable cause, when they are collectively likely.

        The test is on **combined** mass, not on any single cause. On a murky
        card payment the base rates put roughly 8% on a dead card, 6% on a risk
        decline and 5% on a cancellation: no one of them looks alarming and
        together they are a one-in-five chance that re-presenting this payment
        is something we would refuse to do if we knew. Testing each in
        isolation missed all three, which is how 25 forbidden retries survived
        two attempts to stop them.

        Used to fill ``alternative_cause`` when a classifier is unsure and has
        not named a runner-up itself. An honest abstention says "I do not
        know"; the base rates can still say "and on this context it could
        easily be a dead card", and the gate can act on that.

        Without it, abstaining was *less safe* than guessing: `unknown` is a
        retryable cause, so an honest answer routed to a playbook that retries,
        and the model arm ran more forbidden retries than the keyword arm it
        beat on accuracy. Being right about your own uncertainty should not
        cost someone a re-presentment against a cancelled card.
        """
        from ..domain import NEVER_RETRY

        risky = [(c, m) for c, m in distribution if c in NEVER_RETRY]
        if not risky or sum(m for _, m in risky) < min_mass:
            return None
        return max(risky, key=lambda pair: pair[1])[0]

    def __len__(self) -> int:
        return len(self.exemplars)

    @property
    def distinct_wordings(self) -> int:
        return len(self._by_text)


def build_history(params, *, seed: int, months_before: int = 6,
                  start: _dt.datetime | None = None):
    """Simulate the merchant's already-resolved past.

    A separate population from a separate seed, ending before the evaluation
    window opens, restricted to error wordings that were in use at the time.
    """
    from sim import build_population  # local: sim is not a dependency of wapas

    if start is None:
        start = _dt.datetime(2026, 6, 1, tzinfo=_dt.UTC)
    began = start - _dt.timedelta(days=30 * months_before)
    population = build_population(
        params, run_seed=seed, start=began, established_signals_only=True
    )
    return ResolvedHistory.from_episodes(population.episodes)
