"""Right to erasure, implemented as redaction rather than deletion.

There is a genuine tension between two requirements this project makes:

* the audit chain must be **tamper-evident**, so historical rows cannot be
  removed — the ``audit_entry`` FK onto ``episode`` is ``ON DELETE RESTRICT``
  precisely so that deleting an episode is not a back door to deleting its
  trail; and
* a data subject must be able to have their **personal data erased**.

Deleting the rows would satisfy the second and destroy the first. Keeping
everything would satisfy the first and ignore the second.

The resolution: **erasure removes the personal data, not the record of what
happened.** Contact identifiers on the counterparty are nulled, the display
name is replaced with a stable pseudonym, and a tombstone is appended to the
chain. The chain itself needs no modification, because it never held the
plaintext — only salted digests (see :mod:`wapas.audit.chain`).

What survives an erasure is a counterparty with no identifying information, and
an audit trail proving what the system did and when. That is the right answer
for both requirements: the merchant can still show a regulator that contact
caps and consent were honoured, and the individual is no longer identifiable.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .models import Counterparty


@dataclass(frozen=True, slots=True)
class ErasureResult:
    counterparty_id: uuid.UUID
    pseudonym: str
    fields_cleared: tuple[str, ...]
    episodes_retained: int


ERASABLE_FIELDS = ("phone", "email", "display_name")


def erase_counterparty(
    session: Session, counterparty_id: uuid.UUID, *, at: _dt.datetime
) -> ErasureResult:
    """Redact a counterparty's personal data in place.

    Idempotent: erasing an already-erased counterparty is a no-op that returns
    the same pseudonym.

    The caller is responsible for appending the tombstone to the audit chain —
    erasure is itself an audited event, and this function deliberately does not
    reach into the chain so that the ordering stays under the engine's control.
    """
    cp = session.get(Counterparty, counterparty_id)
    if cp is None:
        raise KeyError(f"no counterparty {counterparty_id}")

    pseudonym = f"erased:{counterparty_id.hex[:12]}"
    cleared = tuple(f for f in ERASABLE_FIELDS if getattr(cp, f, None) not in (None, "", pseudonym))

    cp.phone = None
    cp.email = None
    cp.display_name = pseudonym
    cp.channel_consent = []
    cp.opted_out_at = cp.opted_out_at or at
    """Erasure implies opt-out. A counterparty we cannot identify is one we
    must never contact again."""

    episodes = len(cp.episodes)
    session.flush()
    return ErasureResult(
        counterparty_id=counterparty_id,
        pseudonym=pseudonym,
        fields_cleared=cleared,
        episodes_retained=episodes,
    )
