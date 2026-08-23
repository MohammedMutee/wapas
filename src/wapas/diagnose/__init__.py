"""Cause classification: the one place a model is allowed to form an opinion.

Everything else in Wapas is deterministic. The planner selects a playbook by
cause, the gate applies policy, the ledger adds up. The model's entire job is
to decide *why* a payment failed, and even that answer is validated against a
closed taxonomy before anything acts on it.

Keeping the model to one narrow, checkable judgement is the whole design. It
means a bad model degrades recovery rate rather than causing harm, and it means
the interesting question — does the model beat a good keyword classifier on
ambiguous text? — has a clean answer.
"""

from .cache import DEFAULT_PATH, DiagnosisCache
from .diagnoser import DiagnoserStats, LLMDiagnoser
from .prompt import SYSTEM, build_user_prompt
from .schema import DiagnosisResponse

__all__ = [
    "DEFAULT_PATH",
    "SYSTEM",
    "DiagnoserStats",
    "DiagnosisCache",
    "DiagnosisResponse",
    "LLMDiagnoser",
    "build_user_prompt",
]
