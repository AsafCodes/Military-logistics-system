"""
Shared UTC time handling for every stored and emitted timestamp.

Kept dependency-free, like enums.py, so both models.py and the routers can
import it without a cycle.

Why a TypeDecorator instead of `Column(DateTime(timezone=True))`: SQLAlchemy's
SQLite dialect ignores `timezone=True` entirely -- it compiles to plain
DATETIME, strips tzinfo on write WITHOUT converting, and always reads back
naive. Dev and the entire pytest suite run on SQLite, so writing aware values
while reads come back naive raises TypeError the moment anything subtracts
one datetime from another. UtcDateTime's `impl` stays plain `sa.DateTime` on
purpose -- the emitted DDL does not move, so this change ships with no Alembic
revision. DATA-H1-2 is what moves `impl` to `DateTime(timezone=True)` via
load_dialect_impl, and it owns the migration that goes with it.

IMPORTANT -- process_result_value only normalises on a DB round trip. A value
assigned in Python and read back before the session expires it (or with
expire_on_commit=False) comes straight from the identity map, still naive as
Python understands it, and arithmetic against it raises. This decorator is
NOT a safety net for a missed call site: every `datetime.utcnow()` in the
codebase must move to `clock.utcnow()` in the same commit that introduces
this module, or the gap surfaces as a runtime TypeError rather than a silently
wrong value. The production write paths are safe today only because a
commit() (and its expire) happens to sit between every assign and every read.
"""
from datetime import datetime, timezone
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    """Aware UTC now. The replacement for datetime.utcnow() everywhere."""
    return datetime.now(timezone.utc)


class UtcDateTime(TypeDecorator):
    """A DateTime column that is always aware UTC in Python.

    Storage format is unchanged from a plain `Column(DateTime)`: naive UTC.
    Only what Python sees on either side of the column changes.
    """

    impl = sa.DateTime
    cache_ok = True

    def process_bind_param(self, value: Optional[datetime], dialect) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is not None:
            # Aware -> convert to UTC, then drop tzinfo: exactly today's
            # storage format, so existing rows and this column agree.
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        # Naive -> trusted as already UTC (the only convention this codebase
        # has ever used), stored as-is.
        return value

    def process_result_value(self, value: Optional[datetime], dialect) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


def iso_z(value: Optional[datetime]) -> Optional[str]:
    """Serialize an aware (or naive-assumed-UTC) datetime as '...Z'.

    For the two hand-built dicts in routers/reports.py only, which have no
    response_model and so bypass Pydantic's own '...Z' serialization -- this
    makes their output match every other endpoint's wire format instead of
    emitting '+00:00' via jsonable_encoder.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
