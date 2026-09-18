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

@router.get("/setup/fault_types")
def get_fault_types(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_active_user)):
    faults = db.query(models.FaultType).all()
    return [{"id": f.id, "name": f.name, "is_pending": f.is_pending} for f in faults]

@router.get("/setup/fault_types/pending")
def get_pending_fault_types(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_active_user)):
    """Get fault types that are pending manager approval."""
    # can_add_category and can_remove_category become one verb: they name the
    # identical set today, so two verbs would differ in name only -- see
    # Capability's note, which also records that this file is where the claim
    # that a catalog verb was UNENFORCEABLE turned out to be wrong.
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
    if db.query(models.FaultType).filter(models.FaultType.name == name).first():
        raise HTTPException(status_code=400, detail="Fault type already exists")
    
    # A question rather than a gate, exactly as report_fault's is_pending is:
    # anyone may PROPOSE vocabulary, and holding the verb is what lets it skip
    # review. may_global, so a no narrows the write instead of refusing it.
    # Note this route stays open to every authenticated user by design -- it
    # is the front door of the approval workflow, which API-H6 separately
    # records as having no way to drain.
    is_manager = authz.may_global(db, current_user.id, Capability.MANAGE_CATALOG)
    fault = models.FaultType(
        name=name,
        is_pending=not is_manager,
        requested_by_id=current_user.id
    )
    db.add(fault)
    db.commit()
    
    return {"status": "Created", "id": fault.id, "is_pending": fault.is_pending}

@router.put("/setup/fault_types/{fault_id}/approve")
def approve_fault_type(
    fault_id: int,
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
    
    fault.is_pending = False
    db.commit()
    
    return {"status": "Approved", "id": fault.id}

@router.delete("/setup/fault_types/{fault_id}")
def delete_fault_type(
    fault_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    # can_remove_category, folded into the same verb. Its holders are exactly
    # can_add_category's, so the split would be a declaration rather than a
    # difference. Deletion being destructive is the argument for separating
    # them the day some profile holds one without the other.
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
        # DATA-H13 does NOT close this. An ON DELETE rule turns the violation
        # into a different error rather than into no error, so a route that
        # relied on the pre-check alone would keep this 500 after H13 lands.
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
