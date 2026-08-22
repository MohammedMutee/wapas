"""The simulator.

Produces a seeded synthetic world in which recovery agents can be measured.
Three properties are load-bearing:

**Published parameters.** Everything the simulator does is declared in
``sim/params.yaml``, which is committed. Refusing to hide the generative model
is the credibility play: a reviewer can check we did not tune the world to
flatter the agent, and the sensitivity sweep perturbs every parameter ±30% to
show the result does not depend on the exact values.

**Latent traits are invisible to the agent.** A counterparty has a liquidity
refresh day, an annoyance threshold, a channel preference and a self-recovery
rate. The agent sees only *observable history* — what was tried and what
happened. ``tests/test_sim_isolation.py`` asserts the agent's feature builder
cannot reach a latent field.

**Self-recovery is modelled explicitly.** A meaningful share of failed payments
succeed with no intervention at all. Modelling that is what makes the control
arm necessary and makes our honest numbers smaller than our flattering ones.
"""

from .params import SimParams, load_params
from .populations import B2BBuyer, Consumer, Population, build_population
from .responses import Interaction, ResponseModel

__all__ = [
    "B2BBuyer",
    "Consumer",
    "Interaction",
    "Population",
    "ResponseModel",
    "SimParams",
    "build_population",
    "load_params",
]
