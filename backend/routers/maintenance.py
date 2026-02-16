"""Maintenance Router - Tickets and fix endpoints"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from datetime import datetime
from typing import List, Optional

from ..database import get_db
from ..dependencies import get_current_active_user
from .. import models
from .. import schemas

router = APIRouter(tags=["maintenance"])

@router.get("/tickets/", response_model=List[schemas.TicketResponse])
def get_tickets(
    status_filter: Optional[str] = Query(None, description="Filter by ticket status"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    query = db.query(models.MaintenanceLog).options(
        joinedload(models.MaintenanceLog.equipment),
        joinedload(models.MaintenanceLog.fault_type)
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
    item = db.query(models.Equipment).filter(models.Equipment.id == report.equipment_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Equipment not found")
    
    # Find or create fault type
    fault_type = db.query(models.FaultType).filter(models.FaultType.name == report.fault_name).first()
    if not fault_type:
        is_manager = current_user.profile and current_user.profile.can_change_maintenance_status
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
    if not (current_user.profile and current_user.profile.can_change_maintenance_status):
        if current_user.role != "master":
            raise HTTPException(status_code=403, detail="Permission denied: Cannot fix equipment")
    
    item = db.query(models.Equipment).filter(models.Equipment.id == equipment_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Equipment not found")
    
    item.status = "Functional"
    
    # Close open tickets
    db.query(models.MaintenanceLog).filter(
        models.MaintenanceLog.equipment_id == equipment_id,
        models.MaintenanceLog.status != "Closed"
    ).update({"status": "Closed", "closed_at": datetime.utcnow()}, synchronize_session=False)
    
    # Log transaction
    log = models.TransactionLog(
        equipment_id=item.id,
        involved_user_id=current_user.id,
        event_type="FIX",
        timestamp=datetime.utcnow()
    )
    db.add(log)
    
    db.commit()
    return {"status": "Fixed", "notes": notes}
