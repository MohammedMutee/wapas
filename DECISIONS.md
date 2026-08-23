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

---

### D13 · The structured-output ladder prefers *prompted* JSON over constrained decoding
**2026-08-22**

Counter-intuitive, and measured rather than assumed. `scripts/probe_models.py`
runs three labelled diagnosis cases against each model in each output mode:

| model | mode | correct | schema ok | avg latency |
|---|---|---|---|---|
| `openai/gpt-oss-120b` | json_schema | — | — | times out |
| `openai/gpt-oss-120b` | json_object | 0/3 | 0/3 | times out |
| **`openai/gpt-oss-120b`** | **prompted** | **3/3** | **3/3** | 15.4s |
| `nvidia/nemotron-3-super-120b-a12b` | json_schema | **0/3** | 1/3 | 8.3s |
| `nvidia/nemotron-3-super-120b-a12b` | json_object | 0/3 | 0/3 | 3.5s |
| **`nvidia/nemotron-3-super-120b-a12b`** | **prompted** | **3/3** | **3/3** | 4.7s |

Two findings, both worth stating plainly:

1. **Schema compliance is not accuracy.** `nemotron-3-super` honours a strict
   JSON schema and returns fast — and got every case wrong while doing it. On a
   single-case probe it classified an error whose description reads
   *"insufficient balance"* as `card_expired_or_invalid`.
2. **Constraining the decoder measurably degraded reasoning quality** on this
   endpoint. The same model, on the same cases, went from 0/3 to 3/3 simply by
   being asked in prose and having its output parsed and validated by us.

So the ladder is ordered by *measured reliability*, not by strength of
guarantee: prompted first, constrained modes as fallback. We give up a
provider-side correctness guarantee we were not actually getting, and keep our
own guarantee — `ask_structured` validates every response against the Pydantic
model regardless of the rung that produced it, retries with the validation
error fed back, and raises rather than returning unvalidated output.

The safety argument is unchanged either way: the policy gate never trusts model
output, whatever produced it.

**Caveat, recorded honestly:** n=3 cases, one run per cell, on an endpoint with
visible variance (`gpt-oss-120b`/json_object answered correctly in 3.4s on one
call and timed out on the next). This is enough to choose a default, not enough
to publish as a model comparison. The full diagnosis evaluation with a proper
confusion matrix lives in `eval/`, and the report will state the sample size.

---

### D14 · Free-tier tokens are priced notionally, not at zero
**2026-08-22**

NVIDIA's developer tier serves these open models at no charge. Booking the cost
line at zero would make "net incremental recovery" meaningless and would
flatter our own numbers — the exact failure mode the evaluation design exists
to prevent. Every open model carries a `notional: true` rate in
`config/rates.yaml` set to a market comparable for its size, and the report
labels the column as notional.

---

### D15 · Erasure is redaction, not deletion
**2026-08-22**

Surfaced by a failing test teardown, which is the best way to find a design
hole. The integration fixture tried to delete its counterparty and could not:
`audit_entry.episode_id` is `ON DELETE RESTRICT`, so an episode with audit
history pins itself permanently. That is the tamper-evidence guarantee working
exactly as intended — and it collides head-on with right-to-erasure.

Resolution: `erase_counterparty` redacts personal data in place (contact
identifiers nulled, display name replaced with a stable pseudonym, consent
cleared, opted-out set) and leaves the record of *what happened* intact. The
audit chain is untouched because it only ever stored salted digests.

Consequence worth knowing: a development database accumulates audit rows that
cannot be deleted, and a deterministic chain re-inserted verbatim will collide
on the unique hash. Tests vary a tag inside the hashed payload. Inconvenient,
and inconvenient in exactly the right way.

---

### D16 · `unknown` is retryable; caution restricts concessions instead
**2026-08-23**

Found by the first batch run, where `baseline_naive` recovered **0%**. The
`unknown` root cause was in `never_retry_causes`, so any strategy that does not
diagnose could never retry — which silently reduced the industry-default
baseline to inaction and would have handed our agent a rigged win.

A bounded retry of a payment the customer already authorised is the least risky
money action in the system, and it is already capped by `max_retries_per_payment`
and `min_gap_between_retries_hours`. Caution under uncertainty should restrict
concessions and escalation, not retries. Removing `unknown` took the naive
baseline from 0% to 53.5% — from strawman to genuine competitor.

---

### D17 · Two windows: how long to act, and how long to watch
**2026-08-23**

Also found by the first batch run: the control arm recovered **0%** when the
simulator says ~17% of episodes recover unaided. Self-recovery was only
evaluated at the current clock, and an arm that takes no actions never advances
its clock. The consequence was that incremental recovery equalled gross
recovery — exactly the over-claim the control arm exists to prevent.

The engine now distinguishes:

* `action_horizon` — how long it is worth *acting*, which varies by root cause
* `watch_until` — how long we *observe* for an outcome, identical for every arm

Conflating them makes untreated arms look worse than they are, which flatters
the treated arm. Both bugs pushed our own numbers up; that is the direction
that matters, and it is why the control arm earns its 10%.

---

### D18 · The report runs an A/A test on itself
**2026-08-23**

`treatment` and `baseline_rules` currently execute the identical strategy, so
the true difference between them is exactly zero. That makes their comparison
an A/A test, and on the current seed **it fails** — the 95% interval excludes
zero when there is nothing to detect.

Rather than remove the row, the report prints it and explains it. The cause is
arm size (~209 against 1,187) combined with a heavy-tailed amount distribution
where a few large recoveries move the mean a long way.

Two consequences, both stated in the report:

1. The comparison that matters — treatment against `baseline_naive` — already
   spans zero. **We cannot presently claim to beat the industry default.**
2. Before the final run: larger baseline arms, assignment stratified by amount
   decile, and the A/A interval published alongside every A/B interval.

A submission that quietly reports only its favourable interval is one bad
question away from collapsing. A submission that ships its own null test is
much harder to attack.

---

### D19 · Stratified allocation, a permanent placebo, and a permutation test
**2026-08-23**

Acting on D18. Three changes to the experiment design, and one thing that
turned out not to be true.

**Allocation is stratified by amount decile.** Episodes are ranked by amount
into ten equal-count strata and dealt to arms within each stratum by
largest-remainder apportionment. Every arm now receives the same amount profile
to within one episode per decile, on every run, instead of only in expectation.

**Arms rebalanced 60/10/10/10/10 → 40/15/15/15/15, and the population raised
from 2,000 to 5,000 episodes.** A comparison is only as precise as its smaller
arm, so the old split spent its sample on the arm that needed it least. The
simulator runs 5,000 episodes in under a second; there was never a reason to be
short of power.

**The decision rule is now a two-sided stratified permutation test**, with the
bootstrap interval kept as description. Labels are shuffled within the same
deciles the design allocated within, because that is the randomisation the
experiment actually performed.

**A placebo split, reported beside every claim.** The treatment arm is cut into
two stratified halves that ran the same strategy on the same seed, so the true
difference is zero by construction. Unlike the `treatment` vs `baseline_rules`
row it stays a valid null after the LLM lands — the harness must not lose its
null control at the exact moment it starts making claims.

**What did not survive contact with evidence.** The plan was to say that
stratification fixes the A/A failure. `make calibrate` says otherwise: run over
many worlds, both designs reject at broadly similar rates, and the difference
between them is not itself significant. Two separate checks — 400 random
re-splits of a single fixed world, and a synthetic i.i.d. rehearsal — both put
the permutation test at ~5%, which is what theory says: conditional on the
data, a randomisation test is exact whatever generated that data.

So the D18 failure was **an ordinary 1-in-20 event on one seed**, not a broken
procedure, and stratification buys **precision, not calibration**. The
calibration report says so in those words. The original claim would have been
more impressive and less true.

---

### D20 · The harness was reading the answer key
**2026-08-23**

`EpisodeRunner` computed how long an episode could be worked as
`DISPOSITIONS[ep.true_cause].default_horizon_hours` — from the *true* root
cause, which no strategy can observe.

Two consequences, in opposite directions and both bad:

* Every arm was handed a cause-aware stopping rule for free. Our agent looked
  good at knowing when to give up without ever having decided to.
* The fixed-ladder baseline had its schedule silently truncated using knowledge
  it does not have. Its reminder at T+96h was cut off on causes whose horizon
  was shorter, and on `risk_declined` and `customer_cancelled` — horizon 0 — it
  was stopped before it could act at all.

The action window is now a single uniform figure from policy
(`triage.action_window_hours`, 14 days), identical for every arm and
independent of the cause. Knowing when to stop is something a strategy has to
earn. `test_the_action_window_does_not_depend_on_the_true_cause` asserts that a
cause-blind strategy performs the same number of actions whatever the cause is.

Effect: `baseline_naive` rose from 53.3% to 56.5% recovery. Removing the leak
strengthened a competitor, which is the direction that tells you the leak was
real.

---

### D21 · Two fixes found while losing, and why that needs care
**2026-08-23**

After D20 the industry-default baseline was **beating** the rules-only planner,
significantly: −₹2,62,120 gross per 1,000 episodes, p = 0.027. Investigating
turned up one fault on each side. Fixing faults discovered while losing is
where self-serving reasoning enters a project, so both are recorded with their
effect on the numbers.

**The naive baseline could not contact anyone.** Its reminder was hardcoded to
SMS, which was denied on 352 of 750 episodes for `no_channel_consent` and
`channel_not_permitted_at_rung_1`. It was losing on a technicality rather than
on strategy, and a baseline that cannot act is not a baseline. Switched to
email — universally consented here, permitted at rung 1. This made our
competitor **stronger**: naive went from 56.5% to 64.9%.

**The simulator allowed impossible recoveries.** `customer_cancelled` and
`mandate_revoked` had no entry in the retry column of `cause_fit`, so both fell
through to the generic +0.9 retry lift. A silent re-presentment was recovering
a deliberately cancelled payment about half the time, and a *revoked mandate
could be debited* — that one is not improbable, it is impossible; the gateway
rejects it. Corrected to −3.0 and −8.0. This made our competitor **weaker**,
which is why it is spelled out here rather than buried in a diff.

**The planner was under-using its retry budget.** Policy permits three retries
four hours apart; the playbooks used one, on causes where a retry is the single
highest-value action available. It costs nothing, annoys nobody and carries no
opt-out hazard, so declining to use it was not caution. `insufficient_funds`,
`issuer_down`, `technical_timeout` and `mandate_insufficient` now use the
budget. Causes where a retry is worse than useless — authentication drop-off,
dead cards, limit breaches, risk declines — still get zero, and that refusal is
the product.

Net effect: treatment 58.1% → 62.8%, naive 64.9% → 61.9%, and the gap between
them is no longer significant (p = 0.196 on rupees, p = 0.658 on recovery
rate). **We still cannot claim to beat the fixed ladder on rupees.** We can now
claim to beat the blast baseline: +7.52 percentage points, p = 0.0003.

---

### D22 · Opt-outs are priced, separately and visibly
**2026-08-23**

The guardrails could not be shown to pay for themselves, because the only cost
in the ledger was channel spend: ₹432 across 2,000 episodes. An SMS is 12 paise
and a recovered invoice is thousands of rupees, so on that ledger the optimal
strategy is always to contact more — which is exactly backwards, and made the
blast baseline look free.

What disciplines contact frequency is the future revenue destroyed when a
customer opts out. `config/rates.yaml` now prices it: amount × expected future
episodes per year × recoverable share × the share of recovery that needed a
contact channel. Complaints and disputes carry flat handling costs.

Three deliberate constraints, because these are the most contestable numbers in
the project:

1. They are **assumptions, not measurements**, and the report says so where the
   figures appear.
2. They are booked to `externality_paise`, never to `cost_paise`, and audited
   under their own event type. Realised spend and modelled loss are different
   claims and are never summed silently.
3. Net is reported **both with and without** them, so a reader who rejects the
   model entirely can still read every other number in the report.

---

### D23 · What the model is allowed to decide
**2026-08-23**

The model diagnoses. That is the entire list.

It does not choose actions, schedule contacts, set concessions, or decide when
to stop. Actions come from the playbook library, which is data; the policy gate
then independently vets every step against contact, money and escalation rules
the model never sees and cannot influence. A model returning nonsense degrades
the recovery rate and nothing else — it cannot produce a 3 a.m. phone call, a
debit against a revoked mandate, or a fourth message to someone who asked to be
left alone, because none of those are decisions it is permitted to make.

Three consequences worth stating, because each is a design constraint rather
than an accident:

**The ablation is clean.** `baseline_rules` runs the identical planner, gate,
ledger and audit chain with a keyword classifier substituted for the model. The
difference between the two arms is the diagnosis and nothing else, so the
comparison answers "does the model earn its cost?" rather than "is this system
better than that system?"

**The failure mode is bounded and known.** Any failure to obtain a *validated*
diagnosis — transport, schema, budget — degrades to the rules classifier and is
counted. The report prints the fallback rate. A provider outage costs recovery
rate; it does not stop a merchant recovering money and it does not produce
unaudited behaviour.

**The prompt carries no personal data.** Not a phone number, an email, a name,
or a payment reference. Nothing about classifying a decline requires knowing
who the customer is, so sending it to a third-party API would be gratuitous
exposure. A test asserts it.

---

### D24 · Prompt instructions are not controls
**2026-08-23**

The system prompt tells the model, in plain terms, that a failure text like
"Transaction declined" identifies nothing and must be answered `unknown` with
low confidence. Told that explicitly, naming that exact string and that exact
failure mode, the live endpoint answered `gateway_error` at **0.95
confidence**. Tightening the wording moved it to 0.80. It was still wrong, and
still confident.

This matters more than one bad classification: 18% of episodes present an
uninformative signal, and a confident wrong cause is what causes a forbidden
retry against a dead card. An instruction the model can decline to follow is
not a control.

So the check became structural. `DiagnosisResponse` now asks the model to grade
`signal_quality` — specific / weak / generic — *before* choosing a cause, and a
Pydantic validator refuses any answer whose confidence exceeds what its own
grade allows (0.5 for generic, 0.75 for weak). The two statements "this text
names no mechanism" and "I am 95% sure which mechanism it was" contradict each
other, and that contradiction is machine-checkable even though the underlying
judgement is not.

Rejection is not refusal. `ask_structured` feeds the validator's message back
and asks again, and models are markedly better at repairing a named
inconsistency than at avoiding it. If it still cannot produce a consistent
answer, the caller degrades to rules.

The general principle, which applies well beyond this field: **when you want a
model to respect a constraint, find the version of that constraint a validator
can check, and put the model's own self-assessment on the other side of it.**

---

### D25 · Caching model calls, and why the cache is not committed
**2026-08-23**

Amounts reach the model as bands rather than exact figures, and the failure
signals come from a fixed pool, so 5,000 episodes collapse to about 550
distinct prompts. The cache is keyed on the prompt digest, which already covers
the system prompt, the user prompt, the model and the structured mode — so
editing the prompt or switching models invalidates every entry automatically
rather than quietly serving answers to a different question.

Banding amounts is a deliberate trade. It costs the model a signal it could in
principle use, and it buys three things: `make eval` becomes reproducible
despite a non-deterministic API, a run costs a few hundred calls instead of
five thousand, and a free endpoint is not hammered.

**The cache is gitignored.** A committed cache would let the repository produce
the headline number without making a single model call, which is shipping a
conclusion rather than the evidence for it. `make warm` rebuilds it in a couple
of minutes.

---

### D26 · The model was not hanging. We were starving it.
**2026-08-23**

Three separate times this project recorded `openai/gpt-oss-120b` as "hanging"
or "timing out" on the NVIDIA endpoint — twice in the D13 bake-off, once while
warming the diagnosis cache. Each time the conclusion was the same: the free
tier is flaky.

It was not flaky. Reading the raw response envelope instead of the parsed
content settled it in one call:

```
max_tokens=60   74.8s  finish_reason=length  content=''   reasoning_content='The user asks…'
max_tokens=900   2.8s  finish_reason=stop    content='{"ok": true}'
```

It is a reasoning model. It emits `reasoning_content` before `content`, and
both draw on the same output budget. Given too small a budget it spends the
whole thing thinking, stops mid-thought, and returns an empty completion after
a long wait — which through a client that only reads `content` is
indistinguishable from a dead endpoint.

Three changes:

* `ModelProfile.min_output_tokens`, raised by the provider on every call. The
  floor is a property of the model, so it belongs in the model's profile rather
  than at each call site.
* An empty completion with `finish_reason=length` now raises a **non-retryable**
  error naming the actual cause. Retrying an identical starved request three
  times only delays the diagnosis.
* An empty completion for any other reason stays retryable — that one really is
  the endpoint.

The lesson generalises past this model. **"The provider is flaky" is a
conclusion, and it needs the same evidence as any other conclusion.** It was
comfortable, it was repeatable, it explained the symptom, and it was wrong —
and because it was comfortable it survived two bake-offs unexamined. The fix
took one look at a field the client was discarding.

---

### D27 · The model is chosen. It is not yet justified.
**2026-08-23**

`scripts/bakeoff_diagnosis.py` replaces the three-case probe of D13 with 52
labelled cases drawn from the simulator, stratified across all 13 root causes,
scored on four axes rather than one.

| Model | Accuracy | Harmful errors | Failed calls | p50 | Confidence gap |
|---|---|---|---|---|---|
| `rules_only` (no model) | 73.1% | 4 | 0 | — | +0.34 |
| `openai/gpt-oss-120b` | 78.8% | 4 | 0 | 19.4s | +0.32 |
| `nvidia/nemotron-3-super-120b-a12b` | 78.8% | 5 | 0 | 12.3s | +0.33 |
| `nvidia/nemotron-3.5-lightning-30b-a3b` | 51.9% | 4 | 15 | 27.2s | +0.41 |

**Selected: `nvidia/nemotron-3-super-120b-a12b`,** with `gpt-oss-120b` as the
fallback. Accuracy is identical; nemotron is 1.6× faster and needs half the
output budget, which at 550 prompts per cold cache is an hour of wall clock.
The one extra harmful error is a single case and decides nothing. Lightning is
disqualified on all three of the axes that are not accuracy: 15 failed calls,
the worst accuracy, and the slowest responses.

**What this does not show.** The models are 3 cases out of 52 ahead of the
keyword classifier. That is not a difference anyone should act on. A 52-case
run can separate a working model from a broken one — it did, decisively, for
lightning — and it cannot establish that a model beats a good keyword table.

It is worth being precise about why the gap is so small, because it is the
central design question of this project rather than a disappointment. On
failure text that names a mechanism, a keyword table containing the ISO 8583
codes is *very* hard to beat: `baseline_rules` scores 85.7% there. The entire
case for a model lives in the 18% of failures whose text says only that
something failed, where the rules fall to 25.0% because all they can do is
guess the modal cause. A 52-case sample contains roughly nine of those.

So the question "does the model earn its cost?" is not answered here and is not
going to be answered by a bigger bake-off. It is answered by the treatment arm
classifying two thousand episodes in `results/report.md`, split by whether the
question was answerable. Selection is what this file is for. Proof is not.

---

### D28 · Caution is not the same as inaction
**2026-08-23**

D16 established that `unknown` is retryable: a bounded retry of an
already-authorised payment is the least risky money action available, and it is
capped by `max_retries` and `min_gap_between_retries_hours`. The policy said
so. The `unknown` playbook then sent one email and closed the episode.

So the stance and the planner disagreed, and the planner won — which is the
only outcome that ever happens when a principle lives in a comment and its
opposite lives in a data structure.

It went unnoticed while `unknown` was rare. Since `sim/signals.py` started
emitting realistic text, 18% of failures carry no diagnostic signal at all, so
`unknown` is now a common and frequently *correct* answer rather than an
occasional admission of defeat. A planner that closes on it throws away every
genuinely uncertain episode — and, worse, it **punishes an honest classifier
relative to one that guesses confidently**, which is precisely the incentive
this project spent D24 trying to remove from the model.

The playbook now notifies, retries twice within the policy cap, then closes.
Caution stays where harm is possible — concessions, escalation, repeated
contact — and comes off the one action that cannot hurt anyone.

Effect on the rules-only arm: recovery 52.2% → 58.9%, and it is no longer
significantly behind the fixed-ladder baseline (p 0.038 → 0.087 on rupees,
0.013 → 0.158 on recovery rate). It applies identically to every arm that
diagnoses, so it changes the level rather than the comparison.

---

### D29 · The sweep found a parameter that does nothing
**2026-08-23**

`eval/sensitivity.py` scales each parameter group by ±30% and re-runs the whole
evaluation, 25 runs in all. Two things it confirmed, and one it exposed.

**Confirmed.** The agent beats the randomised control arm in all 25 runs —
₹6.43L to ₹13.36L incremental per 1,000 episodes, which is the honest range to
quote rather than the central figure. And the harm gap holds everywhere: the
fixed ladder performs 17× to 28× as many forbidden retries under every
perturbation, because that follows from it not looking at the cause rather than
from any number in a parameter file.

**Sharpened.** The rules planner trails the fixed ladder on rupees in **25 of
25 runs** and reaches significance in none of them. Both halves matter. On the
usual rule there is nothing to report; but a consistently negative sign across
independent perturbations is worth more than any single p-value, so the honest
reading is that it is probably slightly behind and this sweep cannot say by how
much.

**Exposed.** `issuer_outages.bursts_per_90_days` changes nothing. Moving it from
14 to 10 or 18 leaves every headline figure identical.

That parameter is attached to one of this project's more heavily defended
design choices: outages are modelled as *correlated bursts* rather than i.i.d.
draws, on the argument — correct — that independent downtime would let a fixed
ladder do nearly as well as a cause-aware one and make timing intelligence look
worthless. The argument is sound. It is also currently doing no work.

Nothing consumes the correlation. Every episode is diagnosed and planned in
isolation, so what reaches the response model is only this payment's distance
from its own outage ending — a marginal quantity the burst count does not
change. The clustering is real in the data and invisible to the agent.

What would make it matter is an agent that reads *across* episodes: a spike of
`issuer_down` failures on one bank is observable, and the right response is to
hold every retry against that issuer until it recovers rather than
rediscovering the outage one episode at a time. That is a genuine capability
and a genuine product feature. It is not built, so until it is, the bursty
outage model is a property of the simulator and not something the results
depend on — and the sweep says so in those words.

A knob that changes nothing is not reassurance. It is either a result that
genuinely does not depend on it, or a mechanism wired to nothing, and the only
way to tell is to look.

**One more, on the analysis itself.** The first version of this section flagged
four inert parameters, three of which were prices. A price cannot move gross
recovery by construction, so testing it on gross reports every rate in the card
as inert. Each knob is now judged on the metric it can actually move. A
self-critical check that produces false findings is worse than none, because it
spends the credibility it was supposed to earn.
