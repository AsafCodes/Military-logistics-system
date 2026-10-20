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
import functools
import logging
from datetime import date, datetime, time, timedelta, timezone
from importlib.resources import files
from typing import Optional
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.types import TypeDecorator

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    """Aware UTC now. The replacement for datetime.utcnow() everywhere."""
    return datetime.now(timezone.utc)


# --- Local calendar days (DATA-M9) -------------------------------------------
#
# Everything above stores and sends UTC, and still does. What follows is the
# one place a local zone enters: the helpers that bound a group's calendar
# day by midnight in that group's zone (authz.Group.timezone), converted to
# UTC so it can be compared against the stored instants. DATA-M9-2's report
# is their first reader.

# The zone a group gets when nothing names one: the migration's backfill for
# groups that predate the column, the seed's, the bootstrap's unless
# BOOTSTRAP_ROOT_TIMEZONE says otherwise, and zone_or_default's fallback when
# a stored name has left the list.
DEFAULT_TIMEZONE = "Asia/Jerusalem"


@functools.lru_cache(maxsize=1)
def _known_zones() -> frozenset:
    """The zone names the tzdata package lists: the same on every machine.

    The package's own list, not zoneinfo.available_timezones(). That one
    also takes in every zone file under the system's zone directories, so
    it differs between machines: on Linux it lists whatever else sits
    there, such as a file named `localtime`. The package is held to one
    version by the hash lock, so the image, CI and a Windows host accept
    exactly the same names.
    """
    return frozenset(files("tzdata").joinpath("zones").read_text(encoding="utf-8").split())


def zone(name: str) -> ZoneInfo:
    """The zone named, or ValueError. For write paths: a bad name is refused.

    Checked against the list above, not by trying ZoneInfo(name) alone.
    ZoneInfo accepts names that are not on it, and which ones depends on
    the machine: on Windows, where a name is looked up as a file of the
    tzdata package, 'utc' and 'Asia/Jerusalem ' (trailing space) both load.
    A name stored because one machine accepted it could then fail on the
    next.
    """
    if not isinstance(name, str) or name not in _known_zones():
        raise ValueError(f"unknown time zone: {name!r}")
    return ZoneInfo(name)


def zone_or_default(name: str) -> tuple[ZoneInfo, str]:
    """(zone, effective name) for a stored name. For read paths: never raises.

    A name that was valid when it was stored could leave the list in a later
    tzdata release. IANA seldom removes a name (a renamed zone stays as a
    link), but a stored name can also be one written by raw SQL. Refusing
    then would turn every read of that group's day into a 500, so this
    falls back to DEFAULT_TIMEZONE and logs a warning naming the stored
    value.
    The EFFECTIVE name is returned beside the zone so a caller can send it on,
    and a client formats times in the zone that actually bounded the day,
    not the one that was asked for.
    """
    try:
        return zone(name), name
    except ValueError:
        logger.warning(
            "stored time zone %r is not a zone tzdata lists; using %s",
            name, DEFAULT_TIMEZONE,
        )
        return ZoneInfo(DEFAULT_TIMEZONE), DEFAULT_TIMEZONE


def day_bounds(tz: ZoneInfo, d: date) -> tuple[datetime, datetime]:
    """The local calendar day `d` in `tz`, as a half-open UTC range [start, end).

    Both ends are a local midnight converted to UTC: `d`'s and the next
    day's. Not 24 elapsed hours after the start: a day that changes the
    clock is 23 or 25 hours long, and the next day must begin exactly where
    this one ends.

    A midnight the clock skips or repeats (a zone that changes the clock at
    00:00) is read with fold=0, so the day starts at the earliest instant
    that is on it.
    """
    start = datetime.combine(d, time(0), tzinfo=tz)
    end = datetime.combine(d + timedelta(days=1), time(0), tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def today_in(tz: ZoneInfo) -> date:
    """The calendar date it is now in `tz`."""
    return utcnow().astimezone(tz).date()


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
        # where an aware UTC one serializes as 'Z', which would split the one
        # wire format H1-1 unified. Normalising here fixes it at the single
        # point every read passes through.
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
