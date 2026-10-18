"""Reports Router - Inventory and daily movement reports"""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select, union
from sqlalchemy.orm import Session, joinedload
from datetime import date
from typing import List, Optional

from ..database import get_db
from ..dependencies import (
    get_current_active_user,
    scope_equipment_query,
)
from ..enums import Capability, EquipmentStatus, Sensitivity
from .. import authz
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


# One detail for every group the caller may not read, so the answer says
# nothing about which ids exist. See authz.require for the ordering rule.
NO_GROUP = "No such group."


def _report_group(db: Session, user: models.User, group_id: Optional[int]):
    """The group whose day the caller asked for, resolved within their VIEW.

    Named: it must exist and lie in extent(user, VIEW), and both failures are
    the same 404. Not named: the caller's one top VIEW group
    (authz.top_view_groups). With several the route does not guess: it
    answers 400 and lists them -- their own grants, so nothing is disclosed --
    and the client asks again with one. With none, the 404.
    """
    if group_id is not None:
        group = db.get(authz.Group, group_id)
        if group is None or not authz.may(db, user.id, Capability.VIEW, group.id):
            raise HTTPException(status_code=404, detail=NO_GROUP)
        return group

    tops = authz.top_view_groups(db, user.id)
    if not tops:
        raise HTTPException(status_code=404, detail=NO_GROUP)
    if len(tops) > 1:
        return JSONResponse(status_code=400, content={
            "detail": "Several groups are in view; name one with group_id.",
            "groups": [{"id": g.id, "name": g.name} for g in tops],
        })
    return tops[0]


def _day_arm(column, groups, start, end, user):
    """The ids of the log rows filed under `groups` through `column`, in [start, end).

    Classification is DATA-H3-3's rule from dependencies.scope_equipment_query,
    asked of the row's group rather than of the item's: a CLASSIFIED item's
    row is shown only when the caller's VIEW_CLASSIFIED extent covers the
    group the row is filed under. Sensitivity is the item's as it stands now;
    nothing records it per event. is_distinct_from for the reason given there.
    """
    log, item = models.TransactionLog, models.Equipment
    return (
        select(log.id)
        .join(item, log.equipment_id == item.id)
        .where(
            column.in_(groups),
            log.timestamp >= start,
            log.timestamp < end,
            or_(
                item.sensitivity.is_distinct_from(Sensitivity.CLASSIFIED.value),
                column.in_(authz.extent(user.id, Capability.VIEW_CLASSIFIED)),
            ),
        )
    )


@router.get(
    "/reports/daily_movement",
    response_model=schemas.MovementReport,
    responses={
        400: {"description": "No group_id, and several groups are in view; lists them in `groups`."},
        404: {"description": "The group does not exist or is not in view, or nothing is."},
    },
)
def get_daily_movement_report(
    group_id: Optional[schemas.DbInt] = Query(None),
    # `date` on the wire; the parameter is renamed only so it does not
    # shadow the type.
    day: Optional[date] = Query(None, alias="date"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """One group's calendar day of movements, in the group's own zone (DATA-M9).

    This was every visible movement in the 24 hours before the request: a
    window that moved with the clock, straddled midnight, and could not show
    a past day.

    THE DAY is [start, end) between two local midnights of the group's zone,
    from clock.day_bounds, so a day the clock changes on is 23 or 25 hours.
    `date` defaults to today there, and a later date is refused. The zone is
    read through clock.zone_or_default: a stored name tzdata no longer lists
    bounds the day by DEFAULT_TIMEZONE, logs a warning, and the response
    names the zone actually used.

    THE ROWS are the ones filed under the group or anything it contains:
    group_id (where the event left the item) or from_group_id (where a move
    took it from), so a transfer out of the group shows in its day as well as
    in the receiver's. Both are written with the row (audit_trail.record_event)
    and never change, so a past day keeps its rows whatever the item does
    next. One SELECT per column, each over its composite index, and UNION
    rather than UNION ALL: a move between two groups inside this one matches
    both arms, and UNION returns it once. (The legacy Query below would drop
    the duplicate too, since it uniques its rows under joined eager loading,
    but the SQL does not lean on that.)

    NOT SCOPED THROUGH THE ITEM any more, which is a deliberate widening. The
    gate is VIEW over the group, and the group's history includes items that
    have since left the caller's sight; those rows stay in the day. SEC-L9
    records the related point that the report names people the roster hides.
    Classification still applies, per row (_day_arm). A caller who holds
    nothing but the item in their hands (no VIEW grant) has no group to read
    and gets the 404.
    """
    group = _report_group(db, current_user, group_id)
    if isinstance(group, JSONResponse):
        return group

    tz, zone_name = clock.zone_or_default(group.timezone)
    today = clock.today_in(tz)
    if day is None:
        day = today
    if day > today:
        raise HTTPException(status_code=422, detail=f"date is after today ({today.isoformat()}) in {zone_name}.")
    try:
        start, end = clock.day_bounds(tz, day)
    except OverflowError:
        # A date in the first days of year 1, east of UTC: its midnight is
        # before the first datetime Python can hold.
        raise HTTPException(status_code=422, detail="date is out of range.") from None

    groups = authz.descendant_ids([group.id])
    log = models.TransactionLog
    in_day = union(
        _day_arm(log.group_id, groups, start, end, current_user),
        _day_arm(log.from_group_id, groups, start, end, current_user),
    ).subquery()
    logs = (
        db.query(log)
        # A join, not `id IN (...)`. With the IN, Postgres read the whole log
        # and made the eight eager-load joins below for every row before
        # narrowing it to the day: 226 ms at 200,000 rows, against 0.4 ms
        # once the day's ids are joined first and drive the query (measured
        # with pgserver; see tests/test_foreign_key_indexes.py). SQLite
        # planned both forms well.
        .join(in_day, log.id == in_day.c.id)
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
        .order_by(log.timestamp.desc(), log.id.desc())
        .all()
    )

    # schemas.MovementReport, and MovementReport in frontend/src/types/index.ts,
    # key for key; each row is schemas.MovementReportItem.
    # tests/test_movement_report.py holds the frontend types to both.
    return {
        "group_id": group.id,
        "group_name": group.name,
        "timezone": zone_name,
        "date": day,
        # Aware UTC; the response model writes the 'Z'.
        "start": start,
        "end": end,
        "items": [{
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
        } for row in logs],
    }
