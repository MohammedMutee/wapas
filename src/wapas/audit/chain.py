"""The hash chain itself.

Design notes
------------
*Canonical JSON.* Hashing a serialised payload only means something if the
serialisation is deterministic. We sort keys, forbid NaN, use compact
separators, and render every value through a normaliser that maps datetimes to
UTC ISO-8601 with explicit offset, UUIDs to their canonical string form, and
:class:`~decimal.Decimal` to a plain string. Two processes on two machines must
produce byte-identical output for the same logical payload.

*PII.* Audit payloads store digests, not raw personal data. :func:`redact`
replaces any value under a key in :data:`SENSITIVE_KEYS` with a salted digest,
so the chain proves *what happened to whom* without becoming a copy of the
customer database. The salt lives outside the chain, which means a holder of
the chain alone cannot reverse the digests.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import hashlib
import json
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

GENESIS_HASH = "0" * 64
"""Predecessor hash of the first entry in any chain."""

SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "phone", "phone_number", "msisdn", "email", "email_address",
        "name", "full_name", "contact_name", "address", "vpa", "upi_id",
        "card_number", "last4", "account_number", "ifsc", "pan", "gstin",
        "message_body", "reply_text",
    }
)
"""Keys whose values are digested rather than stored verbatim."""


def _normalise(value: Any) -> Any:
    """Map a Python value onto a canonical, JSON-serialisable form."""
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime in audit payload; all times must be tz-aware")
        return value.astimezone(_dt.UTC).isoformat(timespec="microseconds")
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, float):
        # Floats are permitted for model confidences and probabilities, never for
        # money. Round to a fixed precision so tiny platform differences in the
        # last bits cannot change a hash.
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("non-finite float in audit payload")
        return round(value, 12)
    if isinstance(value, dict):
        return {str(k): _normalise(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalise(v) for v in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if hasattr(value, "model_dump"):  # pydantic
        return _normalise(value.model_dump(mode="python"))
    return str(value)


def canonical_json(payload: Any) -> str:
    """Deterministic JSON encoding used for every hash in the chain."""
    return json.dumps(
        _normalise(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def digest(value: str, *, salt: str = "") -> str:
    """Salted digest used to reference personal data without storing it."""
    return hashlib.sha256(f"{salt}|{value}".encode()).hexdigest()[:32]


def redact(payload: Any, *, salt: str = "") -> Any:
    """Recursively replace sensitive values with salted digests.

    >>> redact({"phone": "+919812345678", "amount_paise": 249900})["phone"].startswith("sha256:")
    True
    """
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for k, v in payload.items():
            if str(k).lower() in SENSITIVE_KEYS and isinstance(v, (str, int)):
                out[str(k)] = f"sha256:{digest(str(v), salt=salt)}"
            else:
                out[str(k)] = redact(v, salt=salt)
        return out
    if isinstance(payload, (list, tuple)):
        return [redact(v, salt=salt) for v in payload]
    return payload


def compute_hash(*, seq: int, at: _dt.datetime, prev_hash: str, payload: Any) -> str:
    """The chain commitment.

    ``hash = sha256(prev_hash || seq || iso8601(at) || canonical_json(payload))``

    ``seq`` and ``at`` are inside the commitment so entries cannot be reordered
    or back-dated without detection.
    """
    if len(prev_hash) != 64:
        raise ValueError(f"prev_hash must be 64 hex chars, got {len(prev_hash)}")
    material = "|".join(
        [
            prev_hash,
            str(seq),
            at.astimezone(_dt.UTC).isoformat(timespec="microseconds"),
            canonical_json(payload),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One immutable record in the chain."""

    seq: int
    at: _dt.datetime
    episode_id: uuid.UUID | None
    actor: str
    """``system`` | ``llm`` | ``policy`` | ``human`` | ``provider``"""
    event_type: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str

    def recompute(self) -> str:
        return compute_hash(seq=self.seq, at=self.at, prev_hash=self.prev_hash, payload=self.payload)

    @property
    def is_intact(self) -> bool:
        return self.recompute() == self.hash


@dataclass
class HashChain:
    """In-memory chain builder.

    The database-backed chain uses the same primitives; this class is what the
    engine writes through, and what the tests exercise.
    """

    salt: str = ""
    entries: list[AuditEntry] = field(default_factory=list)

    @property
    def head(self) -> str:
        return self.entries[-1].hash if self.entries else GENESIS_HASH

    def append(
        self,
        *,
        at: _dt.datetime,
        actor: str,
        event_type: str,
        payload: dict[str, Any],
        episode_id: uuid.UUID | None = None,
    ) -> AuditEntry:
        """Append an entry. Payload is redacted before it is hashed or stored."""
        seq = len(self.entries) + 1
        safe = redact(payload, salt=self.salt)
        entry = AuditEntry(
            seq=seq,
            at=at,
            episode_id=episode_id,
            actor=actor,
            event_type=event_type,
            payload=safe,
            prev_hash=self.head,
            hash=compute_hash(seq=seq, at=at, prev_hash=self.head, payload=safe),
        )
        self.entries.append(entry)
        return entry

    def __iter__(self) -> Iterator[AuditEntry]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)


@dataclass(frozen=True, slots=True)
class ChainVerification:
    """Result of walking a chain."""

    ok: bool
    checked: int
    first_break_seq: int | None = None
    reason: str | None = None

    def __str__(self) -> str:
        if self.ok:
            return f"chain intact: {self.checked} entries verified"
        return f"CHAIN BROKEN at seq={self.first_break_seq}: {self.reason} ({self.checked} checked)"


def verify_chain(entries: Iterable[AuditEntry]) -> ChainVerification:
    """Walk a chain and report the first inconsistency.

    Detects: a mutated payload, a mutated timestamp, a deleted entry (sequence
    gap), a reordered entry, and a forged hash.
    """
    prev_hash = GENESIS_HASH
    expected_seq = 1
    checked = 0

    for entry in entries:
        if entry.seq != expected_seq:
            return ChainVerification(
                False, checked, entry.seq,
                f"sequence gap: expected {expected_seq}, found {entry.seq}",
            )
        if entry.prev_hash != prev_hash:
            return ChainVerification(
                False, checked, entry.seq, "prev_hash does not match the preceding entry",
            )
        if entry.recompute() != entry.hash:
            return ChainVerification(
                False, checked, entry.seq, "payload or timestamp altered after write",
            )
        prev_hash = entry.hash
        expected_seq += 1
        checked += 1

    return ChainVerification(True, checked)
