"""Recovery strategies — the thing being compared.

Every arm of the experiment is a strategy implementing the same interface, so
the engine, the policy gate, the cost ledger and the outcome attribution are
*identical* across arms. The only thing that varies is the decision-making.
That is what makes the comparison fair, and it is why the baselines live here
rather than in the evaluation harness.

============================  ==============================================
strategy                      what it represents
============================  ==============================================
:class:`DoNothing`            the randomised control arm. Measures how much
                              revenue recovers with no intervention at all.
:class:`NaiveRetry`           the industry default: a fixed retry ladder that
                              ignores why the payment failed.
:class:`Blast`                maximum aggression. Expected to win on gross
                              recovery and lose on net, opt-outs and
                              complaints — which is the argument for guardrails.
:class:`RulesOnly`            a well-written cause-aware expert system. The
                              honest ablation for "does the LLM earn its cost?"
============================  ==============================================
"""

from .base import Strategy, StrategyContext
from .baselines import Blast, DoNothing, NaiveRetry
from .rules import RulesOnly

__all__ = ["Blast", "DoNothing", "NaiveRetry", "RulesOnly", "Strategy", "StrategyContext"]
