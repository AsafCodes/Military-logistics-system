"""Setup Router - System initialization and fault types"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_active_user
from ..enums import Capability
from .. import authz
from .. import models
from .. import schemas

router = APIRouter(tags=["setup"])


def _tickets_holding(db: Session, fault_type_id: int) -> int:
    """How many maintenance tickets reference this fault type.

    Count rather than load: the number is the whole answer, and the tickets
    themselves are none of the delete route's business.

    Status is deliberately not filtered. A CLOSED ticket still holds the
    reference, so "block only the open ones" would let the foreign key fail by
    a narrower door. The constraint does not care whether the work finished.
    """
    return db.query(models.MaintenanceLog).filter(
        models.MaintenanceLog.fault_type_id == fault_type_id
    ).count()


def _still_in_use(name: str, count: int) -> HTTPException:
    """The one refusal both the pre-check and the commit-time backstop raise.

    Built in one place because the two paths describe the SAME condition and a
    caller must not be able to tell which one answered -- a second spelling
    would drift, and the drift would be a disclosure about timing.

    Names the fault type: global vocabulary any authenticated user can already
    enumerate through GET /setup/fault_types, so nothing new is disclosed.
    Never the table, the column or the constraint -- that leak is half of what
    DATA-H7 reports, and audit_trail.set_status's 409 sets the precedent.
    """
    return HTTPException(
        status_code=409,
        detail=(
            f"Fault type '{name}' is used by {count} maintenance "
            "ticket(s) and cannot be deleted."
        ),
    )

@router.get("/groups", response_model=list[schemas.GroupResponse])
def list_groups(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """List every group, for admin assignment (H1-12 -- replaces list_profiles)."""
    # Gated the same as assigning one (users.update_user_group): placing
    # someone anywhere in the org chart needs to see the whole chart, and
    # MANAGE_PERSONNEL is already global rather than scoped, matching "the
    # personnel table belongs to no unit" in create_user.
    authz.require_global(db, current_user.id, Capability.MANAGE_PERSONNEL)

    return db.query(authz.Group).order_by(authz.Group.id).all()

@router.patch("/groups/{group_id}", response_model=schemas.GroupResponse)
def update_group(
    group_id: schemas.DbInt,
    body: schemas.GroupUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """Set a group's time zone (DATA-M9-1). API only: no page calls it yet.

    Gated like GET /groups, and for the same reason: the org chart belongs
    to no unit, so the verb is MANAGE_PERSONNEL held globally. The gate runs
    before the lookup, so a caller without it learns nothing about which ids
    exist: a well-formed request from them gets 403 for every id, real or
    not. (A malformed body or id is refused first, with 422, by FastAPI.)

    Only this group changes. A zone is stored on every group and never
    inherited (authz.Group.timezone), so its children keep theirs, and each
    is set on its own. Nothing stores a day's bounds, so a reader of a day
    (DATA-M9-2's report is to be the first) will bound it in the zone the
    group has at that moment, past days included.
    """
    authz.require_global(db, current_user.id, Capability.MANAGE_PERSONNEL)

    group = db.get(authz.Group, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")

    group.timezone = body.timezone
    db.commit()
    db.refresh(group)
    return group

@router.get("/setup/fault_types")
def get_fault_types(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_active_user)):
    faults = db.query(models.FaultType).all()
    return [{"id": f.id, "name": f.name, "is_pending": f.is_pending} for f in faults]

@router.get("/setup/fault_types/pending")
def get_pending_fault_types(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_active_user)):
    """Get fault types that are pending manager approval."""
    # can_add_category and can_remove_category became one verb: they named
    # the identical set, so two verbs would have differed in name only -- see
    # Capability's note, which says when that changes and also records that
    # this file is where the claim that a catalog verb was UNENFORCEABLE
    # turned out to be wrong.
    #
    # require_global because a FaultType belongs to the whole force. The
    # approval queue is one shared list, not one per unit, so the authority to
    # read it is authority over the graph rather than over any part of it.
    authz.require_global(db, current_user.id, Capability.MANAGE_CATALOG)
    
    faults = db.query(models.FaultType).filter(models.FaultType.is_pending == True).all()
    return [{"id": f.id, "name": f.name, "is_pending": f.is_pending} for f in faults]

@router.post("/setup/fault_types")
def create_fault_type(
    name: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    # SEC-M14. A gate, and before the lookup for approve's reason: the names
    # are enumerable through GET /setup/fault_types, so the 400 below discloses
    # nothing a refused caller could not read anyway. This route was open to
    # every authenticated user, which let any of them insert rows without
    # limit; it has no frontend caller, and the way an ordinary user proposes a
    # fault type is report_fault, which mints one when a report names it.
    authz.require_global(db, current_user.id, Capability.MANAGE_CATALOG)

    if db.query(models.FaultType).filter(models.FaultType.name == name).first():
        raise HTTPException(status_code=400, detail="Fault type already exists")

    # Pending whoever asks, the holder included. Holding the verb used to let a
    # proposal skip review, and the same verb approves, so one account both
    # proposed and approved. Now every new type waits, and approve_fault_type
    # refuses the account named here: review always takes a second person.
    fault = models.FaultType(
        name=name,
        is_pending=True,
        requested_by_id=current_user.id
    )
    db.add(fault)
    db.commit()
    
    return {"status": "Created", "id": fault.id, "is_pending": fault.is_pending}

@router.put("/setup/fault_types/{fault_id}/approve")
def approve_fault_type(
    fault_id: schemas.DbInt,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    # Gate before lookup, and deliberately so -- the opposite of every
    # equipment route. There the 404 had to come first because it hid whether
    # an id existed; a FaultType is global vocabulary that every unit shares
    # and any user can already enumerate through GET /setup/fault_types, so
    # there is no existence to conceal and nothing to order against.
    authz.require_global(db, current_user.id, Capability.MANAGE_CATALOG)

    fault = db.query(models.FaultType).filter(models.FaultType.id == fault_id).first()
    if not fault:
        raise HTTPException(status_code=404, detail="Fault type not found")

    # SEC-M14, the two-person rule. The proposer never approves, whether the
    # type came from create_fault_type or from report_fault, and whether or not
    # it is still pending: one rule with no exceptions is easier to trust than
    # one that is right only for the rows it was thought about for. A type
    # with no proposer (requested_by_id NULL: the seeded vocabulary) is
    # approvable by any holder, as before. After the 404, because a missing
    # row has no proposer to compare.
    if fault.requested_by_id == current_user.id:
        raise HTTPException(
            status_code=403,
            detail="A fault type cannot be approved by the account that proposed it.",
        )

    fault.is_pending = False
    db.commit()
    
    return {"status": "Approved", "id": fault.id}

@router.delete("/setup/fault_types/{fault_id}")
def delete_fault_type(
    fault_id: schemas.DbInt,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    # can_remove_category, folded into the same verb. Its holders were exactly
    # can_add_category's, so the split would have been a declaration rather
    # than a difference. SEC-M14 kept it here on purpose: the 409 below
    # already limits deletion to a type no ticket has ever used, and a
    # narrower verb would be held by the same two seeded accounts. The day
    # someone must be able to approve fault types without deleting them,
    # gate this on a verb of its own -- Capability's note says how that day
    # shows up.
    authz.require_global(db, current_user.id, Capability.MANAGE_CATALOG)
    
    fault = db.query(models.FaultType).filter(models.FaultType.id == fault_id).first()
    if not fault:
        raise HTTPException(status_code=404, detail="Fault type not found")

    # DATA-H7. maintenance_logs.fault_type_id references this row, so deleting
    # a type any ticket has ever used violates the foreign key. Unhandled, that
    # surfaced as a 500 carrying the constraint's name -- the leak being half of
    # what the ticket reports -- and left the session dirty behind it.
    #
    # Read the name BEFORE the delete: after a rollback the instance is expired,
    # and refreshing it to build an error message is a query that can fail in
    # its own right.
    name = fault.name

    in_use = _tickets_holding(db, fault.id)
    if in_use:
        raise _still_in_use(name, in_use)

    try:
        db.delete(fault)
        db.commit()
    except IntegrityError:
        # The pre-check above is check-then-act and cannot be atomic: a report
        # filed between the count and this commit passes it and still meets the
        # constraint here. Narrow, and the same defect if left to surface raw,
        # so the refusal is issued from both places rather than only the one
        # that is easy to reach from a test.
        #
        # DATA-H13 did NOT close this. It declared the constraint ON DELETE
        # RESTRICT, which names the refusal rather than removing it, so a
        # route relying on the pre-check alone would still answer 500 here.
        #
        # Catching IntegrityError whole is safe only because exactly one
        # foreign key points at fault_types. tests/test_fault_type_deletion.py
        # pins that -- a second referencing table fails there and sends the
        # reader here, because then this branch would be reporting the wrong
        # reason for the refusal.
        db.rollback()

        # And the recount is what makes the claim honest rather than assumed.
        # This branch may only say "tickets hold it" when tickets actually do:
        # an IntegrityError this route cannot explain -- a dependency that
        # starts writing earlier in the request, a constraint added later --
        # must not be dressed up as a conflict naming a count of zero. Re-raise
        # it instead and let it be the loud, wrong-looking 500 it is, because a
        # plausible 409 is the version nobody investigates.
        count = _tickets_holding(db, fault_id)
        if not count:
            raise

        raise _still_in_use(name, count)

    return {"status": "Deleted"}
