# Decision log

Short records of choices that were not obvious, with the reasoning. Reviewers
read these more closely than feature lists.

---

### D1 · Track 03, not the Open Track
**2026-08-21**

The Buildathon is a hiring funnel, not a prize hackathon. A structured-track
submission doubles as a work sample in the domain of the job, and each track's
"the bar" is effectively a published rubric. The Open Track's bar — "evidence
that it creates value" — forces the reviewer to invent a standard, and
ambiguity in a screening process resolves against the candidate.

---

### D2 · The headline metric is *net incremental* recovery, pre-registered
**2026-08-21**

A large share of failed payments recover on their own. Reporting gross recovery
claims credit for customers the agent never touched. 10% of episodes are
randomly assigned to an untreated control arm; the headline is the difference,
minus every rupee of channel and token cost.

Pre-registering the primary metric in the README removes any suspicion of
metric-shopping after the fact.

---

### D3 · The policy gate is deterministic Python, never a model call
**2026-08-21**

A language model can be persuaded; a policy engine cannot. This is what lets
the red-team result be a number (`0 escapes`) instead of an impression. Cost:
the gate cannot handle novel situations gracefully. Accepted — an unknown
precondition fails closed, which is the correct bias for a system that moves
money.

---

### D4 · Guardrails are YAML, and two of them cannot be configured away
**2026-08-21**

Policy as data makes the escalation ladder inspectable, diffable, and
renderable on the dashboard. But `require_valid_mandate_for_debit: false` and
`skip_rungs: true` raise at load time. Some rules are not configuration.

---

### D5 · MODIFY as a first-class verdict, not just ALLOW/DENY
**2026-08-21**

A message scheduled at 22:10 IST is rewritten to 08:00 rather than dropped. The
recovery still happens and it happens legally. A gate that can only refuse
turns every compliance rule into lost revenue, which is how compliance ends up
being switched off in real systems.

---

### D6 · The exits are ungated and free
**2026-08-21**

`escalate_to_human` and `close_episode` bypass every check — including the kill
switch — and do not consume the action budget. An agent that can be prevented
from stopping, or from asking a human for help, is a bug. Surfaced by a
property test: without the budget exemption, a heavily constrained episode
could run out of budget before it could close itself.

---

### D7 · Integer paise everywhere
**2026-08-21**

Floats never touch money. A rounding error in the single number the project is
judged on is not a bug we are willing to risk. Rupees exist only at the
presentation boundary.

---

### D8 · A virtual clock from day one, not retrofitted
**2026-08-21**

Reproducibility is the pitch, and it is very hard to add later. Nothing outside
`wapas.clock` may call `datetime.now()`. 90 simulated days run in seconds and
the same seed always produces the same sequence.

---

### D9 · Audit payloads store salted digests, not personal data
**2026-08-21**

The chain must prove what happened to whom without becoming a copy of the
customer database. The salt lives outside the chain, so possession of the chain
alone does not permit reversal.

---

### D10 · Checkout abandonment cut, in writing
**2026-08-21**

Listed in the track brief, and deliberately not built: weakest ground truth of
the five surfaces, most saturated idea in the applicant pool, and it shares no
new machinery with the three surfaces we do build. Declared cuts read as
judgment; silent omissions read as oversight. See `docs/scope.md`.

---

### D11 · Compliance rules carry `verified: false` when unconfirmed
**2026-08-21**

Citing a regulation incorrectly to a payments company is worse than not citing
one. Unverified rules are enforced exactly as strictly, but never presented as
regulatory citations. `PolicyBundle.unverified_rules()` surfaces them.

---

### D12 · Hand-rolled state machine over LangGraph or Temporal
**2026-08-21**

A second infrastructure system, or a framework whose checkpointing needs
explaining anyway, against ~400 lines that a reviewer can read in one sitting.
Revisit if the week-2 schedule slips.
