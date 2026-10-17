"""Reports Router - Inventory and daily movement reports"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from datetime import timedelta
from typing import List, Optional

from ..database import get_db
from ..dependencies import (
    get_current_active_user,
    scope_equipment_derived_query,
    scope_equipment_query,
)
from ..enums import EquipmentStatus
from .. import clock
from .. import models
from .. import schemas

router = APIRouter(tags=["reports"])

@router.get("/reports/query", response_model=List[schemas.InventoryReportItem])
def get_inventory_report(
    equipment_type: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    # DATA-H12-2, and a DELIBERATE behaviour change rather than a tightening
    # with no visible effect. This was Optional[str] fed into a SQL equality
    # test, so `?status=Functinoal` answered 200 with an empty list -- the
    # caller cannot tell "nothing is broken" from "you misspelled the filter",
    # which is the same silent-typo failure DATA-H12 is about, on the read side.
    # FastAPI now answers 422 naming the four accepted values.
    #
    # The empty spelling changes too: `?status=` used to be falsy and skip the
    # filter, and is now a 422 like any other non-member. The app never sends it --
    # the only frontend caller of this route passes no params at all
    # (GeneralReportPage.tsx).
    #
    # The two neighbours stay Optional[str] on purpose: both are ilike
    # substring searches over open vocabularies, so there is nothing to
    # enumerate and a misspelling there legitimately matches nothing.
    status: Optional[EquipmentStatus] = Query(None),
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
    if status is not None:
        # `.value`, not the member. A str-mixin enum binds as its own string
        # data so both spellings happen to work here, but `str()` of a member
        # is "EquipmentStatus.FUNCTIONAL" -- so the day this moves into an
        # f-string or a driver that stringifies, the bare member would compare
        # against a value no row holds and silently match nothing.
        q = q.filter(models.Equipment.status == status.value)
    if holder_name:
        q = q.join(models.User, models.Equipment.holder_user_id == models.User.id).filter(
            models.User.full_name.ilike(f"%{holder_name}%")
        )

    items = q.order_by(models.Equipment.id.asc()).all()

    # Each row is schemas.InventoryReportItem, which the response_model holds
    # it to, and InventoryReportItem in frontend/src/types/index.ts, key for
    # key; tests/test_report_item_contract.py holds the frontend type to it.
    result = []
    for item in items:
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
            # The item's compliance level, as every other route sends it
            # (DATA-M7). This used to rename GOOD to "Reported" and pass the
            # other two through, a vocabulary of its own.
            "reporting_status": item.compliance_level,
            "last_reporter": item.holder.full_name if item.holder else "",
            # Aware UTC from clock.UtcDateTime, so the response model writes
            # the same '...Z' that clock.iso_z used to produce here.
            "last_verified_at": item.last_verified_at,
        })
    
    return result

def _name(user):
    return user.full_name if user else None


@router.get("/reports/daily_movement", response_model=List[schemas.MovementReportItem])
def get_daily_movement_report(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    cutoff = clock.utcnow() - timedelta(hours=24)
    
    # SEC-H5. Every movement of every item, force-wide, to any authenticated
    # user -- who holds what, when it changed hands, and where it went.
    #
    # Scoped through the EQUIPMENT the log refers to, not through the log: a
    # transaction is only as visible as the item it describes. The join is
    # inner on purpose here, unlike DATA-M10's complaint about optional
    # filters -- a log whose equipment row is gone describes nothing anyone
    # can be authorised to see.
    log = models.TransactionLog
    logs = scope_equipment_derived_query(
        db.query(log)
        .options(
            joinedload(log.equipment),
            # DATA-M8-2. Every name below comes from one of these, and a lazy
            # load there is a query per row per name.
            joinedload(log.actor),
            joinedload(log.from_holder),
            joinedload(log.to_holder),
            joinedload(log.from_owner),
            joinedload(log.to_owner),
            joinedload(log.from_group),
            joinedload(log.to_group),
        )
        .filter(log.timestamp >= cutoff),
        log,
        current_user,
    ).order_by(log.timestamp.desc()).all()

    # Each row is schemas.MovementReportItem, which the response_model holds
    # it to, and MovementReportItem in frontend/src/types/index.ts, key for
    # key; tests/test_movement_report.py holds the frontend type to it.
    return [{
        "id": row.id,
        # Aware UTC from clock.UtcDateTime; the response model writes the 'Z'.
        "timestamp": row.timestamp,
        "event_type": row.event_type,
        "serial_number": row.equipment.serial_number if row.equipment else None,
        # The actor. This was a constant None until DATA-M8-2.
        "reporter_name": _name(row.actor),
        "from_holder_name": _name(row.from_holder),
        "to_holder_name": _name(row.to_holder),
        "from_owner_name": _name(row.from_owner),
        "to_owner_name": _name(row.to_owner),
        "from_group_name": row.from_group.name if row.from_group else None,
        "to_group_name": row.to_group.name if row.to_group else None,
        "from_location": row.from_location,
        "to_location": row.to_location,
        "reason": row.reason,
        "location": row.location,
    } for row in logs]
