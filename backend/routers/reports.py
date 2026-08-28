"""Reports Router - Inventory and daily movement reports"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from datetime import datetime, timedelta
from typing import List, Optional

from ..database import get_db
from ..dependencies import (
    get_current_active_user,
    get_daily_status,
    scope_equipment_derived_query,
    scope_equipment_query,
)
from .. import models

router = APIRouter(tags=["reports"])

@router.get("/reports/query")
def get_inventory_report(
    equipment_type: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    holder_name: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    q = db.query(models.Equipment).options(
        joinedload(models.Equipment.catalog_item),
        joinedload(models.Equipment.holder),
        joinedload(models.Equipment.owner),
        # Not optional. unit_association below reads the group's name, and
        # a lazy load there is one query per row across the whole report.
        joinedload(models.Equipment.group),
    )

    # Visibility, from the one definition. This route used to carry its own
    # copy of the scoping ladder, and that copy had already drifted from the
    # original (it omitted the unit_path fallback) -- DATA-H9. A second
    # implementation is the defect; sharing the first one is the fix.
    q = scope_equipment_query(q, current_user)

    # Apply user filters
    if equipment_type:
        q = q.join(models.CatalogItem).filter(models.CatalogItem.name.ilike(f"%{equipment_type}%"))
    if location:
        q = q.filter(models.Equipment.custom_location.ilike(f"%{location}%"))
    if status:
        q = q.filter(models.Equipment.status == status)
    if holder_name:
        q = q.join(models.User, models.Equipment.holder_user_id == models.User.id).filter(
            models.User.full_name.ilike(f"%{holder_name}%")
        )

    items = q.order_by(models.Equipment.id.asc()).all()

    # Build response matching frontend GeneralReportItem interface
    result = []
    for item in items:
        compliance = get_daily_status(item.last_verified_at)
        reporting_status = "Reported" if compliance == "GOOD" else compliance
        
        result.append({
            "id": item.id,
            "item_type": item.catalog_item.name if item.catalog_item else "Unknown",
            # The group name, since H1-11 dropped the path string this used
            # to read. Same value for every seeded item -- the paths WERE the
            # group names -- but now it comes from the thing that actually
            # decides who sees the row rather than from a column beside it.
            # The `else` is not reachable through this application -- group_id is
            # NOT NULL and the app engine enforces the foreign key -- but it
            # matches how every other line here treats a relationship, and a
            # database migrated in from elsewhere is exactly where a dangling id
            # would come from.
            "unit_association": item.group.name if item.group else "",
            "designated_owner": item.owner.full_name if item.owner else (item.holder.full_name if item.holder else "Unassigned"),
            "actual_location": item.custom_location or "",
            "serial_number": item.serial_number or "",
            "reporting_status": reporting_status,
            "last_reporter": item.holder.full_name if item.holder else "",
            "last_verified_at": item.last_verified_at.isoformat() if item.last_verified_at else None,
        })
    
    return result

@router.get("/reports/daily_movement")
def get_daily_movement_report(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    cutoff = datetime.utcnow() - timedelta(hours=24)
    
    # SEC-H5. Every movement of every item, force-wide, to any authenticated
    # user -- who holds what, when it changed hands, and where it went.
    #
    # Scoped through the EQUIPMENT the log refers to, not through the log: a
    # transaction is only as visible as the item it describes. The join is
    # inner on purpose here, unlike DATA-M10's complaint about optional
    # filters -- a log whose equipment row is gone describes nothing anyone
    # can be authorised to see.
    logs = scope_equipment_derived_query(
        db.query(models.TransactionLog)
        .options(joinedload(models.TransactionLog.equipment))
        .filter(models.TransactionLog.timestamp >= cutoff),
        models.TransactionLog,
        current_user,
    ).order_by(models.TransactionLog.timestamp.desc()).all()
    
    return [{
        "id": log.id,
        "timestamp": log.timestamp.isoformat() if log.timestamp else None,
        "event_type": log.event_type,
        "serial_number": log.equipment.serial_number if log.equipment else None,
        "reporter_name": None,
        "location": log.location
    } for log in logs]
