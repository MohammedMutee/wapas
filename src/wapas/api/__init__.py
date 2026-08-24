"""The running service.

Everything else here is a batch that starts and finishes. This is the part that
stays up, holds episodes open between a link being created and a customer
paying, and reacts when Razorpay tells it something happened.

It reuses the diagnoser, the gate, the actuator and the audit chain rather than
reimplementing any of them. An API with its own copy of the rules would be a
second place for them to disagree, and the whole claim of this project is that
the thing measured and the thing running are the same thing.
"""

from .app import OpenEpisode, Service, build_service, create_app
from .pg_store import PostgresEpisodeStore
from .store import (
    Applied,
    EpisodeStore,
    InMemoryEpisodeStore,
    LiveEpisode,
    apply_event,
)

__all__ = [
    "Applied",
    "EpisodeStore",
    "InMemoryEpisodeStore",
    "LiveEpisode",
    "OpenEpisode",
    "PostgresEpisodeStore",
    "Service",
    "apply_event",
    "build_service",
    "create_app",
]
