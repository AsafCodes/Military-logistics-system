"""Setup Router - System initialization and fault types"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_active_user
from ..enums import Capability
from .. import authz
from .. import models
from .. import schemas

router = APIRouter(tags=["setup"])

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
    
    db.delete(fault)
    db.commit()
    
    return {"status": "Deleted"}
