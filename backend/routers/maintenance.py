"""Maintenance Router - Tickets and fix endpoints"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional

from ..database import get_db
from ..dependencies import (
    get_current_active_user,
    get_scoped_equipment_or_404,
    scope_equipment_derived_query,
    require_status_authority,
)
from ..enums import Capability
from .. import authz
from .. import clock
from .. import models
from .. import schemas

router = APIRouter(tags=["maintenance"])

@router.get("/tickets/", response_model=List[schemas.TicketResponse])
def get_tickets(
    status_filter: Optional[str] = Query(None, description="Filter by ticket status"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    # SEC-H5, and the one H1-9 deferred here by name while cutting the two
    # write routes in this file onto the group model. Scoping those narrowed
    # this by nothing: the leak is the LIST, not the ids in it.
    #
    # A ticket is as visible as the equipment it is about, so the scope comes
    # from the item rather than from the ticket. status_filter still applies
    # on top, and now filters within what the caller can see instead of
    # within the force.
    #
    query = scope_equipment_derived_query(
        db.query(models.MaintenanceLog).options(
            joinedload(models.MaintenanceLog.equipment),
            joinedload(models.MaintenanceLog.fault_type),
        ),
        models.MaintenanceLog,
        current_user,
    )
    if status_filter:
        query = query.filter(models.MaintenanceLog.status == status_filter)
    
    tickets = query.order_by(models.MaintenanceLog.opened_at.desc()).all()
    
    return [schemas.TicketResponse(
        id=t.id,
        equipment_id=t.equipment_id,
        fault_type_id=t.fault_type_id,
        equipment_name=t.equipment.item_name if t.equipment else "Unknown",
        fault_type=t.fault_type.name if t.fault_type else "Unknown",
        description=t.description,
        status=t.status,
        opened_at=t.opened_at,
        closed_at=t.closed_at
    ) for t in tickets]

@router.post("/maintenance/report")
def report_fault(
    report: schemas.ReportFaultRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    # Resolve, then decide, and both ABOVE the find-or-create block below --
    # that block commits, so gating after it would let a refused report leave a
    # permanent FaultType behind under an attacker-chosen name. Same ordering,
    # and the same reason, as create_equipment's catalog block.
    #
    # This route previously checked NOTHING beyond authentication and resolved
    # by raw id, so any authenticated private could mark every item in the force
    # Malfunctioning by counting upwards -- SEC-H6's denial-of-readiness case,
    # which also corrupts the readiness analytics downstream. 404 rather than
    # 403 for an unseen id is the half that stops the counting.
    item = get_scoped_equipment_or_404(db, current_user, report.equipment_id)
    require_status_authority(db, current_user, item)

    # Find or create fault type
    fault_type = db.query(models.FaultType).filter(models.FaultType.name == report.fault_name).first()
    if not fault_type:
        # This was the last profile read in the file, and it is a question
        # rather than a gate: may this reporter mint global vocabulary without
        # review, or does their fault type wait for approval?
        # can_change_maintenance_status answered it before and REPORT_STATUS is
        # where that column went, so every seeded profile gets the answer it
        # got before. may() rather than require() because a no here is not a
        # refusal -- the report is accepted either way.
        #
        # What is new is the possession-only reporter, who passes the gate above
        # by holding the item and holds no grant at all: their novel fault type
        # is now pending, which is what an approval queue is for.
        #
        # Deliberately not RESOLVE_FAULT. That would newly send a company
        # commander's fault types to a queue API-H6 says nothing can drain.
        # FaultType has no group of its own -- see Capability's note on
        # MANAGE_CATALOG -- so the item's group is the only scope available.
        is_manager = authz.may(db, current_user.id, Capability.REPORT_STATUS, item.group_id)
        fault_type = models.FaultType(
            name=report.fault_name,
            is_pending=not is_manager,
            requested_by_id=current_user.id
        )
        db.add(fault_type)
        db.commit()
        db.refresh(fault_type)
    
    # Create maintenance log
    log = models.MaintenanceLog(
        equipment_id=item.id,
        fault_type_id=fault_type.id,
        description=report.description,
        status="Open"
    )
    db.add(log)
    
    # Mark equipment as malfunctioning
    item.status = "Malfunctioning"
    
    db.commit()
    return {"status": "Fault Reported", "ticket_id": log.id}

@router.post("/maintenance/fix/{equipment_id}")
def fix_equipment(
    equipment_id: int,
    notes: str = "",
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    # The gate MOVED from above the lookup to below it, and that is the defect
    # being corrected rather than a tidy-up: asking permission first meant a
    # caller who passed the profile check learned, from the 404, whether any id
    # existed anywhere in the force. Resolve first and an id outside the
    # caller's sight is indistinguishable from one that was never issued.
    #
    # RESOLVE_FAULT, not REPORT_STATUS, and require() rather than
    # require_status_authority: closing a fault is not something possession
    # confers. A soldier holding a broken item may report it and may not
    # declare it fixed, and a company commander may report on their company's
    # kit and may not close the ticket -- the technical function does that.
    # Those two refusals are the only reason the second verb exists.
    item = get_scoped_equipment_or_404(db, current_user, equipment_id)
    authz.require(db, current_user.id, Capability.RESOLVE_FAULT, item.group_id)

    item.status = "Functional"
    
    # Close open tickets
    db.query(models.MaintenanceLog).filter(
        models.MaintenanceLog.equipment_id == item.id,
        models.MaintenanceLog.status != "Closed"
    ).update({"status": "Closed", "closed_at": clock.utcnow()}, synchronize_session=False)

    # Log transaction
    log = models.TransactionLog(
        equipment_id=item.id,
        involved_user_id=current_user.id,
        event_type="FIX",
        timestamp=clock.utcnow()
    )
    db.add(log)
    
    db.commit()
    return {"status": "Fixed", "notes": notes}
