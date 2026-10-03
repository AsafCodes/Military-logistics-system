"""backfill last_verified_at from verification evidence

DATA-H5-2. b047d26 stopped the forgery; this repairs what it left behind.

Until that commit, assign_owner and transfer_equipment both reset
equipment.last_verified_at and the model defaulted it to the moment of
creation. So a stored timestamp does not mean anybody checked the item -- it
may only mean somebody signed a form, or that the row was created. This
revision replaces every stored value with the latest evidence that a physical
inspection actually happened, and clears the column where there is none.

The evidence, and why these two sources are trustworthy back through history:

  - transaction_logs rows with event_type VERIFICATION or CONDITION_REPORT.
    VERIFICATION is the daily presence check and has been written by
    equipment.verify_equipment_daily since before the repository restructure;
    CONDITION_REPORT is younger (DATA-H4-3) but every row it describes also
    left a verifications row, so nothing is lost where it is absent.

  - verifications.created_date. A condition report writes one of these
    unconditionally, including for the years when it wrote no log row at all.

OPERATORS: items with no inspection on record come out NULL, which every
reader renders as "never reported" / SEVERE. On a database whose history is
mostly paperwork this turns a lot of the fleet red on deploy. That is the
ticket's point rather than a side effect -- the green was never earned -- but
it is visible immediately and worth saying out loud before anyone runs this.

WHY TWO STATEMENTS AND NOT ONE
-------------------------------
Both single-statement spellings are portability traps, and the SQLite half of
each one passes, which is what makes them worth naming here:

  - `SELECT MAX(t) FROM (... UNION ALL ...) AS evidence` correlating to
    equipment.id from inside the derived table is accepted by SQLite and
    rejected by Postgres, which requires LATERAL for that shape.

  - `GREATEST(a, b)` ignores NULL arguments on Postgres; SQLite's scalar
    max(a, b) returns NULL if either argument is NULL. Same expression, two
    answers, and the wrong one silently clears a real timestamp.

Two correlated scalar subqueries in SET avoid both, and follow the precedent
in c93f2a615d84, whose group backfill has exactly this shape.

The event-type strings are literals rather than imports from backend.enums.
A revision describes the database as it was when the revision was written, and
an enum that gets renamed later must not retroactively change what this one
did. tests/test_audit_trail.py's test_transfer_still_writes_the_event_type_it
_always_wrote makes the same argument from the other side: the stored strings
are frozen rather than merely current, which is what lets this file name them.

Nothing here routes through clock.UtcDateTime: it is column-to-column SQL
inside the database, so no bind or result processor runs. Since e5f1b8d24a07
all three columns are TIMESTAMPTZ on Postgres and naive ISO-8601 text on
SQLite, and the comparison in the second statement is correct within either.

THIS REVISION IS DATA-ONLY, AND THAT IS LOAD-BEARING rather than incidental.
backend.migrations.BASELINE_STAMP names the revision before it, so a
pre-Alembic database is stamped there and this one still runs -- which is the
only reason the repair reaches the legacy databases that need it. Adding DDL
here breaks that arrangement and is refused by tests/test_group_schema.py's
staleness guard.

A SCHEMA CHANGE AFTER THIS POINT MUST NOT MOVE THE STAMP, which is the
opposite of what this paragraph said until DATA-H12 and the reason the
constant was renamed. Advancing it past this revision is precisely how a
legacy database gets told this backfill already ran. Such a revision instead
has to tolerate running against a database that already has its changes, and
be named in backend.migrations.IDEMPOTENT_SCHEMA_REVISIONS; d3a9c17be540 is
the first, and its docstring carries the reasoning.

The AST guard in tests/test_audit_trail.py -- "only audit_trail.py may assign
last_verified_at" -- walks backend/ and does not see this file. That is right
rather than a gap it misses: the guard exists to stop a sixth ROUTE stamping
the clock as a side effect of paperwork, and this is a one-time repair that
reads evidence already in the database rather than a new write path.

Both equipment_id columns are unindexed, so each subquery scans its table per
equipment row, and the first statement rewrites every equipment row rather
than only the ones whose value changes. Alembic runs the whole revision in one
transaction, so on Postgres that means a new tuple version per row and a write
lock held on all of them until it commits. Irrelevant at this size -- tens of
items -- and the first thing to reconsider before running it against a
database where it is not.

A `WHERE last_verified_at IS DISTINCT FROM (...)` guard would skip the
unchanged rows, and is deliberately not used: that operator reached SQLite only
in 3.39, so it would reintroduce exactly the kind of dialect split the two
statements above exist to avoid, in exchange for a saving this data size
cannot notice.

Revision ID: 6f821fc450b8
Revises: e5f1b8d24a07
Create Date: 2026-09-18 00:00:00.000000

"""
from typing import Sequence, Union

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '6f821fc450b8'
down_revision: Union[str, Sequence[str], None] = 'e5f1b8d24a07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The latest daily presence check or condition report logged against the item.
LOGGED_INSPECTION = (
    "(SELECT MAX(timestamp) FROM transaction_logs"
    "  WHERE transaction_logs.equipment_id = equipment.id"
    "    AND transaction_logs.event_type IN ('VERIFICATION', 'CONDITION_REPORT'))"
)

# The latest condition report filed against it, which is the older and more
# complete of the two records.
FILED_VERIFICATION = (
    "(SELECT MAX(created_date) FROM verifications"
    "  WHERE verifications.equipment_id = equipment.id)"
)


def upgrade() -> None:
    """Upgrade data. No schema change; the column has been nullable since the
    initial revision.

    Unconditional, with no WHERE on the first statement: a row with no evidence
    must be CLEARED, not left holding whatever paperwork last wrote. Skipping
    those rows is the tempting half-measure and it preserves exactly the values
    this revision exists to remove.
    """
    conn = op.get_bind()

    conn.execute(text(
        f"UPDATE equipment SET last_verified_at = {LOGGED_INSPECTION}"
    ))

    conn.execute(text(
        f"UPDATE equipment SET last_verified_at = {FILED_VERIFICATION}"
        f" WHERE {FILED_VERIFICATION} IS NOT NULL"
        f"   AND (last_verified_at IS NULL OR last_verified_at < {FILED_VERIFICATION})"
    ))


def downgrade() -> None:
    """Downgrade -- deliberately nothing, and not because it is hard.

    The old values were unverifiable claims; that is why they were replaced.
    Nothing records what they were, and even if something did, restoring them
    would restore the defect. A downgrade that put them back would hand an
    operator a database that reports compliance nobody earned.

    Empty rather than raising, so the chain still walks backwards for schema
    purposes. c93f2a615d84 and a7226c349ecf both note the same split between
    restoring a shape and restoring data.
    """
