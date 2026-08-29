"""
Shared UTC time handling for every stored and emitted timestamp.

Kept dependency-free, like enums.py, so both models.py and the routers can
import it without a cycle.

Why a TypeDecorator instead of a bare `Column(DateTime(timezone=True))`:
SQLAlchemy's SQLite dialect ignores `timezone=True` entirely -- it compiles to
plain DATETIME, strips tzinfo on write WITHOUT converting, and always reads
back naive. Dev and the entire pytest suite run on SQLite, so writing aware
values while reads come back naive raises TypeError the moment anything
subtracts one datetime from another. The decorator is what makes the two
backends agree in Python regardless of what each one can actually store.

DATA-H1-2 gave the decorator a dialect: Postgres columns are TIMESTAMPTZ and
record the instant themselves, while SQLite keeps H1-1's storage format (naive
UTC) because it has nothing better to offer. See load_dialect_impl below --
the two halves of that split, the column type and the bind parameter, are only
correct together, which is why H1-2 shipped them in one commit with the
Alembic revision that moves the columns.

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


def _stores_timezone(dialect) -> bool:
    """Does this backend keep the zone for us, or must we drop it on the way in?

    One predicate, read by both load_dialect_impl and process_bind_param, so
    the column type and the value bound into it cannot disagree about which
    backend they are talking to. They are wrong in opposite directions if they
    ever do -- see the class docstring.
    """
    return dialect.name == "postgresql"


class UtcDateTime(TypeDecorator):
    """A DateTime column that is always aware UTC in Python.

    Postgres stores TIMESTAMPTZ and is handed aware values. Everything else
    (SQLite, in practice) stores naive UTC and is handed naive values, because
    its DATETIME cannot record a zone.

    The pairing is load-bearing and silently wrong when broken, on Postgres
    only, which no SQLite test can see:

      - aware value -> TIMESTAMP WITHOUT TIME ZONE: Postgres casts through the
        session TimeZone and discards the offset.
      - naive value -> TIMESTAMPTZ: Postgres reads it AS the session TimeZone.

    Either way every timestamp shifts by the server's offset -- DATA-H1's own
    defect, one layer further down. Hence the shared predicate above, and hence
    DATA-H1-2's migration and this class changing in the same commit.
    """

    impl = sa.DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if _stores_timezone(dialect):
            return dialect.type_descriptor(sa.DateTime(timezone=True))
        return dialect.type_descriptor(sa.DateTime())

    def process_bind_param(self, value: Optional[datetime], dialect) -> Optional[datetime]:
        if value is None:
            return None

        # Normalise to aware UTC first, then branch only on representation.
        # A naive input is trusted as already UTC -- the only convention this
        # codebase has ever used -- and it must be STAMPED before it reaches
        # psycopg2, not passed through: a naive datetime handed to TIMESTAMPTZ
        # is read as the session's local time, not as UTC.
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)

        if _stores_timezone(dialect):
            return value
        # Drop tzinfo only where the column cannot hold it. The value is
        # already UTC, so this is the storage format every existing row uses.
        return value.replace(tzinfo=None)

    def process_result_value(self, value: Optional[datetime], dialect) -> Optional[datetime]:
        # No dialect branch: one rule is correct for both backends.
        #
        # astimezone, not a pass-through, for the aware arm. psycopg2 returns a
        # TIMESTAMPTZ in the SESSION's timezone, which is not necessarily UTC --
        # and an aware non-UTC value serializes as '+03:00' through Pydantic
        # while iso_z() emits 'Z', which would split the one wire format H1-1
        # unified. Normalising here fixes it at the single point every read
        # passes through.
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


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
