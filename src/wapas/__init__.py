"""Wapas — revenue recovery agent for Razorpay merchants.

Detect revenue at risk, diagnose the root cause, execute a bounded recovery
workflow under deterministic guardrails, and prove what was recovered.

Package layout mirrors the five-stage loop:

    ingest    →  normalise provider events into ``RiskEvent``
    triage    →  score recoverability, compute expected value, drop negative-EV work
    diagnose  →  classify root cause (LLM, structured output, closed taxonomy)
    plan      →  select a playbook, choose arms via the contextual bandit
    policy    →  the gate: deterministic, non-LLM, vetoes or rewrites every action
    actuators →  idempotent side effects (retry, link, message, escalate)
    engine    →  the durable state machine and scheduler that drives all of it
    audit     →  hash-chained append-only record of every decision
    ledger    →  money in, cost out, net recovered
"""

__version__ = "0.1.0"
