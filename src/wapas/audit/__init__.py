"""Tamper-evident audit trail.

Every decision Wapas makes — including the ones the policy gate refused — is
appended to a hash chain. Two properties follow, and both are claims we make
in the submission:

1. **Tamper-evidence.** Each entry commits to the hash of its predecessor, so
   editing or deleting any historical row breaks verification at that point.
   ``wapas audit verify`` walks the chain and reports the first break.
2. **Replayability.** The chain records the full input to every decision, so
   ``wapas replay <episode>`` can re-derive the decision path and assert the
   reconstruction matches what actually happened.

The database additionally enforces append-only at the storage layer with a
trigger that rejects UPDATE and DELETE (see the initial migration), so the
guarantee does not rest on application code being well-behaved.
"""

from .chain import (
    GENESIS_HASH,
    AuditEntry,
    HashChain,
    canonical_json,
    compute_hash,
    verify_chain,
)

__all__ = [
    "GENESIS_HASH",
    "AuditEntry",
    "HashChain",
    "canonical_json",
    "compute_hash",
    "verify_chain",
]
