"""Model bake-off.

Model choice is an *evaluated* decision here, not a preference. This script
probes candidate models on the NVIDIA catalogue with a labelled diagnosis set
and reports, per model and per structured-output mode: does it answer at all,
does it return parseable JSON, does it match the schema, is it *correct*, how
long does it take, and how many tokens does it burn.

    python scripts/probe_models.py                    # the default candidate list
    python scripts/probe_models.py openai/gpt-oss-120b nvidia/nemotron-3-ultra-550b-a55b

Findings are transcribed into ``NVIDIA_PROFILES`` in
``src/wapas/llm/openai_compat.py``, which is what the structured-output ladder
reads at runtime. Re-run this before trusting those entries — the catalogue
changes, and models listed by ``GET /v1/models`` are not always callable.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wapas.llm.base import ProviderError, StructuredMode
from wapas.llm.openai_compat import UNSERVED, OpenAICompatProvider

CANDIDATES = [
    "openai/gpt-oss-120b",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia/nvidia-nemotron-nano-9b-v2",
    "meta/llama-3.3-70b-instruct",
    "moonshotai/kimi-k3",
]

SYSTEM = (
    "You are a payments failure analyst. Classify the root cause of a failed "
    "Razorpay payment into the given taxonomy. Answer only with JSON."
)

# Labelled probe set. Small on purpose — this is a triage tool for choosing a
# model, not the full diagnosis evaluation (that lives in eval/).
CASES: list[tuple[str, str]] = [
    (
        "insufficient_funds",
        """rail: card
error_code: BAD_REQUEST_ERROR
error_description: "Your card has insufficient balance to complete this payment"
error_source: issuer
error_step: authorization
amount_paise: 249900
hour_of_day_ist: 22
day_of_month: 28
customer_last_5_attempts: 2 failures, both on the 28th of prior months""",
    ),
    (
        "authentication_failed",
        """rail: card
error_code: GATEWAY_ERROR
error_description: "Customer did not complete 3DS authentication within the time limit"
error_source: customer
error_step: authentication
amount_paise: 89900
hour_of_day_ist: 14
customer_last_5_attempts: 1 prior success on UPI""",
    ),
    (
        "risk_declined",
        """rail: card
error_code: BAD_REQUEST_ERROR
error_description: "Payment declined by the issuing bank risk engine"
error_source: issuer
error_step: authorization
amount_paise: 4999900
hour_of_day_ist: 3
customer_last_5_attempts: 4 declines across 3 different cards in 20 minutes""",
    ),
]

TAXONOMY = [
    "insufficient_funds", "authentication_failed", "issuer_down", "gateway_error",
    "technical_timeout", "card_expired_or_invalid", "limit_exceeded", "risk_declined",
]

SCHEMA = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string", "enum": TAXONOMY},
        "confidence": {"type": "number"},
        "recoverable": {"type": "boolean"},
    },
    "required": ["root_cause", "confidence", "recoverable"],
    "additionalProperties": False,
}


def probe(provider: OpenAICompatProvider, model: str, mode: StructuredMode) -> dict:
    correct = parseable = schema_ok = 0
    latency_ms = tokens = 0
    error = ""

    for expected, case in CASES:
        user = case
        if mode is StructuredMode.PROMPTED:
            user += "\n\nRespond with only JSON matching:\n" + json.dumps(SCHEMA)
        try:
            r = provider.complete(
                model=model, system=SYSTEM, user=user, mode=mode,
                schema=SCHEMA if mode is StructuredMode.JSON_SCHEMA else None,
                max_tokens=600,
            )
        except ProviderError as exc:
            error = str(exc)[:70]
            break

        latency_ms += r.latency_ms
        tokens += r.usage.total
        from wapas.llm.structured import extract_json

        try:
            parsed = json.loads(extract_json(r.content))
        except json.JSONDecodeError:
            continue
        parseable += 1
        if set(SCHEMA["required"]) <= set(parsed) and parsed.get("root_cause") in TAXONOMY:
            schema_ok += 1
        if parsed.get("root_cause") == expected:
            correct += 1

    n = len(CASES)
    return {
        "model": model, "mode": str(mode), "error": error,
        "correct": f"{correct}/{n}", "schema_ok": f"{schema_ok}/{n}",
        "parseable": f"{parseable}/{n}",
        "avg_ms": latency_ms // n if latency_ms else 0,
        "avg_tokens": tokens // n if tokens else 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("models", nargs="*", default=CANDIDATES)
    ap.add_argument("--out", default="results/model_bakeoff.md")
    args = ap.parse_args()

    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        print("NVIDIA_API_KEY is unset. `set -a; . ./.env; set +a` first.", file=sys.stderr)
        return 2

    provider = OpenAICompatProvider(api_key=key)
    rows = []
    for model in (args.models or CANDIDATES):
        if model in UNSERVED:
            print(f"skip {model}: listed in the catalogue but 404s on inference")
            continue
        for mode in (StructuredMode.JSON_SCHEMA, StructuredMode.JSON_OBJECT, StructuredMode.PROMPTED):
            row = probe(provider, model, mode)
            rows.append(row)
            flag = "!!" if row["error"] else ("OK" if row["correct"] == f"{len(CASES)}/{len(CASES)}" else "  ")
            print(f"{flag} {model:42} {row['mode']:12} correct={row['correct']} "
                  f"schema={row['schema_ok']} {row['avg_ms']}ms {row['error']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    header = "| model | mode | correct | schema ok | parseable | avg ms | avg tokens | error |"
    lines = [
        "# Model bake-off",
        "",
        f"Probed {len(CASES)} labelled diagnosis cases per model per mode. "
        f"Generated by `scripts/probe_models.py`.",
        "",
        header,
        "|" + "---|" * 8,
    ]
    for r in rows:
        lines.append(
            f"| `{r['model']}` | {r['mode']} | {r['correct']} | {r['schema_ok']} | "
            f"{r['parseable']} | {r['avg_ms']} | {r['avg_tokens']} | {r['error']} |"
        )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
