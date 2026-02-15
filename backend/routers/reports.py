"""Reports Router - Inventory and daily movement reports"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload

from .. import models
from ..database import get_db
from ..dependencies import get_current_active_user, get_daily_status

router = APIRouter(tags=["reports"])


@router.get("/reports/query")
def get_inventory_report(
    equipment_type: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    holder_name: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user),
):
    q = db.query(models.Equipment).options(
        joinedload(models.Equipment.catalog_item),
        joinedload(models.Equipment.holder),
        joinedload(models.Equipment.owner),
    )

    # Apply Visibility Filters (Hierarchy Scoping)
    user_hierarchy = current_user.unit_hierarchy

    # MASTER role always sees everything
    if current_user.role == models.UserRole.MASTER or current_user.role == "master":
        pass
    elif current_user.profile and current_user.profile.can_view_all_equipment:
        pass

    elif current_user.profile and current_user.profile.can_view_battalion_realtime:
        if user_hierarchy:
            parts = user_hierarchy.split("/")
            if len(parts) >= 2:
                bat_path = "/".join(parts[:2])
                q = q.filter(models.Equipment.unit_hierarchy.startswith(bat_path))
            else:
                q = q.filter(models.Equipment.unit_hierarchy.startswith(user_hierarchy))
        else:
            q = q.filter(models.Equipment.holder_user_id == current_user.id)

    elif current_user.profile and current_user.profile.can_view_company_realtime:
        if user_hierarchy:
            q = q.filter(models.Equipment.unit_hierarchy.startswith(user_hierarchy))
        else:
            q = q.filter(models.Equipment.holder_user_id == current_user.id)
    else:
        q = q.filter(models.Equipment.holder_user_id == current_user.id)

    # Apply user filters
    if equipment_type:
        q = q.join(models.CatalogItem).filter(
            models.CatalogItem.name.ilike(f"%{equipment_type}%")
        )
    if location:
        q = q.filter(models.Equipment.custom_location.ilike(f"%{location}%"))
    if status:
        q = q.filter(models.Equipment.status == status)
    if holder_name:
        q = q.join(
            models.User, models.Equipment.holder_user_id == models.User.id
        ).filter(models.User.full_name.ilike(f"%{holder_name}%"))

    items = q.order_by(models.Equipment.id.asc()).all()

    # Build response matching frontend GeneralReportItem interface
    result = []
    for item in items:
        compliance = get_daily_status(item.last_verified_at)
        reporting_status = "Reported" if compliance == "GOOD" else compliance

        result.append(
            {
                "id": item.id,
                "item_type": item.catalog_item.name if item.catalog_item else "Unknown",
                "unit_association": item.unit_hierarchy or "",
                "designated_owner": item.owner.full_name
                if item.owner
                else (item.holder.full_name if item.holder else "Unassigned"),
                "actual_location": item.custom_location or "",
                "serial_number": item.serial_number or "",
                "reporting_status": reporting_status,
                "last_reporter": item.holder.full_name if item.holder else "",
                "last_verified_at": item.last_verified_at.isoformat()
                if item.last_verified_at
                else None,
            }
        )

    return result


@router.get("/reports/daily_movement")
def get_daily_movement_report(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user),
):
    cutoff = datetime.utcnow() - timedelta(hours=24)

    logs = (
        db.query(models.TransactionLog)
        .options(joinedload(models.TransactionLog.equipment))
        .filter(models.TransactionLog.timestamp >= cutoff)
        .order_by(models.TransactionLog.timestamp.desc())
        .all()
    )

    return [
        {
            "id": log.id,
            "timestamp": log.timestamp.isoformat() if log.timestamp else None,
            "event_type": log.event_type,
            "serial_number": log.equipment.serial_number if log.equipment else None,
            "reporter_name": None,
            "location": log.location,
        }
        for log in logs
    ]
