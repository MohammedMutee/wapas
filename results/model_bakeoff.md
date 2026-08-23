# Wapas — diagnosis model bake-off

52 labelled cases from seed `771001`, 4 per root cause, PROMPTED mode, ground truth from the simulator.

Supersedes the three-case probe recorded in `DECISIONS.md` D13. Three cases
cannot separate two models; this is small but it is stratified across every
cause, so a model cannot score well by handling only the common ones.

| Model | Accuracy | Harmful errors | Failed calls | p50 latency | Confidence gap |
|---|---|---|---|---|---|
| `rules_only (no model)` | 73.1% | 4 | 0 | — | +0.34 |
| `openai/gpt-oss-120b` | 78.8% | 4 | 0 | 19.4s | +0.32 |
| `nvidia/nemotron-3-super-120b-a12b` | 78.8% | 5 | 0 | 12.3s | +0.33 |
| `nvidia/nemotron-3.5-lightning-30b-a3b` | 51.9% | 4 | 15 | 27.2s | +0.41 |

**Harmful errors** are cases where the true cause is never-retryable — a dead
card, a risk decline, a revoked mandate — and the model named something
retryable. The planner acts on that, so it is the error that reaches a real
person. It is not symmetric with the reverse mistake and is not averaged into
accuracy.

**Confidence gap** is mean confidence when correct minus mean confidence when
wrong. A model with high accuracy and a gap near zero is still hard to use,
because nothing downstream can tell when to be careful.

### Where `rules_only (no model)` goes wrong

| True cause | Called it | n |
|---|---|---|
| `mandate_insufficient` | `insufficient_funds` | 3 |
| `invoice_disputed` | `invoice_forgotten` | 2 |
| `customer_cancelled` | `authentication_failed` | 2 |
| `issuer_down` | `unknown` | 2 |
| `technical_timeout` | `unknown` | 1 |
| `risk_declined` | `unknown` | 1 |
| `card_expired_or_invalid` | `unknown` | 1 |
| `limit_exceeded` | `unknown` | 1 |

### Where `openai/gpt-oss-120b` goes wrong

| True cause | Called it | n |
|---|---|---|
| `risk_declined` | `unknown` | 2 |
| `invoice_disputed` | `invoice_forgotten` | 2 |
| `issuer_down` | `unknown` | 2 |
| `technical_timeout` | `unknown` | 1 |
| `technical_timeout` | `gateway_error` | 1 |
| `card_expired_or_invalid` | `gateway_error` | 1 |
| `limit_exceeded` | `unknown` | 1 |
| `customer_cancelled` | `authentication_failed` | 1 |

### Where `nvidia/nemotron-3-super-120b-a12b` goes wrong

| True cause | Called it | n |
|---|---|---|
| `risk_declined` | `unknown` | 2 |
| `issuer_down` | `unknown` | 2 |
| `technical_timeout` | `unknown` | 1 |
| `technical_timeout` | `gateway_error` | 1 |
| `invoice_disputed` | `invoice_forgotten` | 1 |
| `invoice_disputed` | `unknown` | 1 |
| `card_expired_or_invalid` | `gateway_error` | 1 |
| `customer_cancelled` | `authentication_failed` | 1 |

### Where `nvidia/nemotron-3.5-lightning-30b-a3b` goes wrong

| True cause | Called it | n |
|---|---|---|
| `customer_cancelled` | `authentication_failed` | 2 |
| `issuer_down` | `unknown` | 2 |
| `technical_timeout` | `unknown` | 1 |
| `risk_declined` | `unknown` | 1 |
| `invoice_disputed` | `invoice_forgotten` | 1 |
| `mandate_insufficient` | `insufficient_funds` | 1 |
| `card_expired_or_invalid` | `unknown` | 1 |
| `limit_exceeded` | `unknown` | 1 |

Reproduce: `python scripts/bakeoff_diagnosis.py --seed 771001 --per-cause 4`
