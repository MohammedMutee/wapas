"""The boundary where reasoning becomes effect.

Everything upstream is inference. This package is the only code that changes
anything outside the process, and it is deliberately small: create a payment
link, read one back, cancel one, and verify what the provider sends us.

The narrowness is the safety property. A recovery agent's blast radius is
whatever its actuators can do, so the list of things they can do is short
enough to read in one sitting — and none of them move money away from the
merchant.
"""

from .base import (
    ActuationRefused,
    ActuationResult,
    IdempotencyStore,
    InMemoryIdempotencyStore,
    idempotency_key,
    require_approval,
)
from .razorpay import RazorpayActuator
from .webhooks import InboundEvent, WebhookRejected, parse, sign, verify

__all__ = [
    "ActuationRefused",
    "ActuationResult",
    "IdempotencyStore",
    "InMemoryIdempotencyStore",
    "InboundEvent",
    "RazorpayActuator",
    "WebhookRejected",
    "idempotency_key",
    "parse",
    "require_approval",
    "sign",
    "verify",
]
