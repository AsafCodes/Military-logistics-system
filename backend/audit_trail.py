"""The one place an equipment state change becomes a record.

DATA-H4. Two audit tables existed and neither was written consistently:
transaction_logs from four inline constructions that had already diverged
(fix_equipment omitted user_status_at_time; two sites set timestamp and two
relied on the column default), equipment_status_history from exactly one.
Ownership assignment wrote nothing at all, so the single most auditable event
in an accountability system left no trace and never reached the movement
report.

The ticket's fix is "route every state mutation through one audit-writing
helper so no path CAN bypass it", and the operative word is `can`. A module
plus a convention is not that -- a convention is what the four divergent call
sites already were. The enforcement is in tests/test_audit_trail.py, which
walks every file under backend/ with `ast` and fails on a construction of
either table, or a write to `.status`, outside this module. That guard is the
fix; this module is only what makes obeying it possible.

What the guard actually promises is that no ORDINARY spelling gets past it,
which is the honest version of "cannot" and the one worth writing down. A
determined author can still alias the class or reach for setattr; the guard
lists what it misses rather than implying it misses nothing. It exists to stop
the sixth call site being written inline by accident, which is how the first
five happened.

WHY set_status OWNS THE ASSIGNMENT
-----------------------------------
It would be smaller to write a helper that records a status change the caller
has already made. It would also be useless: that is precisely the shape that
let report_fault set "Malfunctioning" and write no row for four years. The
assignment and the record are one operation or they are two things that drift,
so `equipment.status = x` is not a statement any router gets to write.

NOT DEPENDENCY-FREE, unlike clock.py and enums.py, which open by saying they
are and mean it. This module imports models, so it sits below it and cannot be
imported by it.

IMPORT STYLE IS LOAD-BEARING. `from . import models`, and every construction
below goes through the module attribute -- never `from .models import
TransactionLog`. The routers bind the module the same way, so
tests/test_equipment_authority.py's monkeypatch of
`equipment_router.models.TransactionLog` reaches through to whatever this
module builds. A direct name binding here would capture the class at import
time, the patch would sail past it, and the test that proves
transfer_equipment's `except HTTPException: raise` still works would pass
while proving nothing.
"""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import clock, models
from .enums import ChangeReason, EventType


def record_event(
    db: Session,
    *,
    equipment: models.Equipment,
    actor: models.User,
    event_type: EventType,
    location: str | None = None,
    recipient: models.User | None = None,
) -> None:
    """Append a transaction_logs row. Adds; never commits.

    The caller's own commit covers it, so an audit row cannot survive a
    rollback of the change it describes -- and, in the other direction, a
    refused write leaves nothing behind (tests/test_status_authority.py's
    test_a_refused_write_leaves_no_row_in_any_table pins that for four routes).
    Call it AFTER the gates and inside whatever transaction the mutation is in.

    Normalises what the four original sites disagreed about: user_status_at_time
    is always recorded and timestamp is always explicit. Nothing reads the
    former yet; it is written because a log that carries it for three events out
    of four answers no question about the fourth.

    `location` is a place and `recipient` is a person, and they are mutually
    exclusive because the column holds one string. A recipient is written as
    "User:{name}" -- transfer_equipment's inherited convention, which encodes a
    person in a column named for a place, and which this function now spells
    rather than each caller.

    That one-line move is the point: the AST guards next door check that rows
    are CONSTRUCTED here, and cannot check that they are FORMATTED alike, so a
    "user:" or "User: " from a later caller would pass every test and quietly
    split the movement report's only record of who received the item. The
    callers DATA-H4-2 and -3 add are exactly the ones that would drift.

    Inherited, not endorsed. involved_location_id, which would carry a place
    properly, is dead (DATA-M18); involved_user_id records the ACTOR, not the
    recipient. So this string is the only place the other party appears at all,
    and DATA-M8 is where that gets fixed structurally.
    """
    if location is not None and recipient is not None:
        raise ValueError("record_event takes a location or a recipient, not both")

    if recipient is not None:
        # full_name is nullable (models.py), so the naive f-string writes the
        # literal "User:None" into the audit column. Equipment.
        # current_state_description guards the same nullability with "Unknown";
        # matching it keeps one vocabulary for an unnamed person.
        location = f"User:{recipient.full_name or 'Unknown'}"

    log = models.TransactionLog(
        equipment_id=equipment.id,
        involved_user_id=actor.id,
        event_type=event_type.value,
        user_status_at_time=actor.is_active_duty,
        timestamp=clock.utcnow(),
        location=location,
    )
    db.add(log)


def set_status(
    db: Session,
    *,
    equipment: models.Equipment,
    actor: models.User,
    new_status: str,
    reason: ChangeReason,
    notes: str | None = None,
    verification_id: int | None = None,
) -> None:
    """Assign equipment.status and write the row that explains it.

    Adds; never commits -- same contract as record_event above. Returns
    nothing, deliberately: an Optional row would invite `if set_status(...)`
    at a call site, re-splitting the very decision this module exists to hold
    in one place.

    A NO-OP IS NOT AN EVENT. equipment_status_history records TRANSITIONS: its
    columns are old_status and new_status, and a row where they are equal
    asserts a change that did not happen. So a caller setting the status it
    already holds gets no row. That is a real gap for one caller and it is
    deliberate: report_fault sets "Malfunctioning" unconditionally, so a second
    fault on an already-broken item is audited in transaction_logs and not
    here. The history table is the item's condition over time, not the list of
    times somebody said something about it -- and the two together are complete.

    AN ABSENT STATUS IS REFUSED, not laundered. equipment.status is nullable
    (models.py) while old_status is NOT NULL, so a row in that state would hand
    None to a NOT NULL column and raise IntegrityError -- a bare 500 from
    report_fault, or worse from transfer_equipment, whose `except Exception`
    re-emits the message with the constraint name embedded in it (SEC-M8's
    shape, at a site DATA-H6 already had to fix once).

    409 rather than a coalesce to "" or "Unknown": the row IS corrupt, and
    inventing an old status to write beside a real new one puts a fabricated
    value into the audit trail to avoid an error message. DATA-H3-1 made the
    same call for a NULL sensitivity -- surface it, do not launder it. Only
    reachable by hand-written SQL today, because nothing in the application
    writes a NULL status; DATA-M12 and DATA-H12 are what would make it
    unreachable by construction.

    `not old_status`, NOT `old_status is None`. The empty string is the same
    corruption wearing different clothes -- it satisfies a NOT NULL column, so
    it does not crash; it just writes a row claiming a transition out of
    nothing, which is worse than the error it avoids. Testing absence one way
    and truthiness the other is DATA-M1's exact defect, named two sections up
    in the same audit, and no real status is falsy: the four EquipmentStatus
    members are all non-empty strings.
    """
    if not new_status:
        # The caller's side of the same coin, and a different KIND of error.
        # An absent old_status is a corrupt row, which a 409 describes; an
        # absent new_status is this module being called wrongly, which no
        # client can fix and no status code should dress up as a conflict.
        # Latent today -- every caller passes an EquipmentStatus value or a
        # literal -- and this module is precisely the chokepoint DATA-H4-2 and
        # -3 add callers to, which is when a silently-empty new_status would
        # start writing rows claiming a transition INTO nothing.
        raise ValueError(
            f"set_status requires a non-empty new_status, got {new_status!r}"
        )

    old_status = equipment.status
    if not old_status:
        raise HTTPException(
            status_code=409,
            detail=(
                "Equipment record has no status; it cannot be changed until "
                "one is set."
            ),
        )

    if new_status == old_status:
        return

    equipment.status = new_status
    history = models.EquipmentStatusHistory(
        equipment_id=equipment.id,
        old_status=old_status,
        new_status=new_status,
        change_reason=reason.value,
        verification_id=verification_id,
        notes=notes,
        created_by=actor.id,
    )
    db.add(history)
