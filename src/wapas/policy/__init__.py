"""The policy gate.

**This package contains no model call and never will.**

A language model can be persuaded. A policy engine cannot. Every action Wapas
takes passes through deterministic Python that has no prompt, no context
window, and no capacity to be talked out of its rules. The model's job is to
propose; the gate's job is to permit.

That separation is what lets the red-team suite report a hard number
(``0 escapes``) rather than an impression, and it is why this package is one of
the two modules held to ``mypy --strict``.
"""

from .config import PolicyBundle, load_policies
from .gate import GateContext, PolicyGate

__all__ = ["GateContext", "PolicyBundle", "PolicyGate", "load_policies"]
