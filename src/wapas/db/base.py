"""Declarative base and shared column types."""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any

from sqlalchemy import BigInteger, DateTime, MetaData, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
"""Deterministic constraint names, so migrations are diffable and reversible."""


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def pk_uuid() -> Any:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def fk_uuid(target: str, *, nullable: bool = False, index: bool = True) -> Any:
    from sqlalchemy import ForeignKey

    return mapped_column(
        UUID(as_uuid=True), ForeignKey(target, ondelete="CASCADE"),
        nullable=nullable, index=index,
    )


def ts(*, nullable: bool = False, index: bool = False) -> Any:
    """A timezone-aware timestamp. There is no other kind in this schema."""
    return mapped_column(DateTime(timezone=True), nullable=nullable, index=index)


def paise(*, nullable: bool = False, default: int | None = 0) -> Any:
    """An integer paise amount. Never NUMERIC, never FLOAT."""
    return mapped_column(BigInteger, nullable=nullable, default=default)


def enum_str(length: int = 40, *, nullable: bool = False, index: bool = False) -> Any:
    """Enums are stored as text.

    Deliberate: a native PG enum requires a migration to add a value, and the
    root-cause taxonomy is the part of this system most likely to grow. The
    Python side is a closed ``StrEnum``, so validation happens there.
    """
    return mapped_column(String(length), nullable=nullable, index=index)


def jsonb(*, nullable: bool = False, default: Any = dict) -> Any:
    return mapped_column(JSONB, nullable=nullable, default=default)


UTC = _dt.UTC
