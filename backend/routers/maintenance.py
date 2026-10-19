"""Maintenance Router - Tickets and fix endpoints"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional

from ..database import get_db
from ..dependencies import (
    get_current_active_user,
    get_scoped_equipment_or_404,
    scope_equipment_derived_query,
    require_status_authority,
)
from ..enums import Capability, ChangeReason, EquipmentStatus, EventType, TicketStatus
from .. import audit_trail
from .. import authz
from .. import clock
from .. import models
from .. import schemas

router = APIRouter(tags=["maintenance"])

@router.get("/tickets/", response_model=List[schemas.TicketResponse])
def get_tickets(
    # DATA-H12-2, the sibling of reports.get_inventory_report's `status` and a
    # deliberate behaviour change for the same reason: an unrecognised filter
    # answered 200 with an empty list, which reads as "no tickets" rather than
    # "no such status". 422 now, naming the four members.
    #
    # Admits IN_PROGRESS and WAITING_PARTS, which no route assigns, so the
    # filter accepts a value that always answers empty. That is correct: the
    # emptiness is then a true statement about the queue rather than a lie
    # about the query, and it is DATA-M22 that gives the first of the two a
    # writer. The frontend already asks for it -- MaintenancePage.tsx renders
    # an "In Progress" tab -- though it filters client-side and only
    # StatsGrid.tsx sends this parameter, always as Open.
    status_filter: Optional[TicketStatus] = Query(None, description="Filter by ticket status"),
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
    if status_filter is not None:
        # `.value`, not the member. A str-mixin enum binds as its own string
        # data so both spellings work today; `str()` of a member is
        # "TicketStatus.OPEN", so the day this moves into an f-string or a
        # driver that stringifies its parameters, the bare member would compare
        # against a value no row holds and silently match nothing.
        # reports.get_inventory_report states the same reason over the same
        # shape of line.
        query = query.filter(models.MaintenanceLog.status == status_filter.value)
    
    tickets = query.order_by(models.MaintenanceLog.opened_at.desc()).all()

    return [_ticket_response(t) for t in tickets]


def _ticket_response(t: models.MaintenanceLog) -> schemas.TicketResponse:
    """One ticket as the list and close_ticket both return it."""
    return schemas.TicketResponse(
        id=t.id,
        equipment_id=t.equipment_id,
        fault_type_id=t.fault_type_id,
        equipment_name=t.equipment.item_name if t.equipment else "Unknown",
        fault_type=t.fault_type.name if t.fault_type else "Unknown",
        description=t.description,
        status=t.status,
        opened_at=t.opened_at,
        closed_at=t.closed_at,
        technician_id=t.technician_id,
    )


def _lock_item(db: Session, item: models.Equipment) -> models.Equipment:
    """Take the item's row lock for the rest of the transaction. DATA-M21.

    The three routes that open or close an item's tickets take it before
    they write a ticket or the item (report_fault after its fault-type
    commit, which would release it), so they run one at a time per item.
    Without it, a close
    that counts the item's open tickets while a report commits a new one can
    set the item Functional with a ticket still open.

    SELECT ... FOR UPDATE on Postgres. SQLite has no row locks and SQLAlchemy
    leaves the clause out there; SQLite takes one write lock for the whole
    database instead.

    populate_existing, because the item was loaded before the lock and
    set_status reads its status as the "from" of the history row. The row as
    it stands once the lock is held is the one to read.
    """
    return (
        db.query(models.Equipment)
        .filter(models.Equipment.id == item.id)
        .with_for_update()
        .populate_existing()
        .one()
    )


def _not_closed():
    """A ticket that is not closed yet, NULL status included.

    is_distinct_from, not `!=`: the column is nullable and its default is
    Python-side only, so raw SQL can store NULL, and `NULL != 'Closed'` is
    NULL, not TRUE. A bare `!=` skipped such a ticket in both the close and
    the count of what is still open, so close_ticket repaired the item, left
    the ticket NULL and committed before failing on the response. The same
    three-valued-logic ruling as dependencies.scope_equipment_query's.
    """
    return models.MaintenanceLog.status.is_distinct_from(TicketStatus.CLOSED.value)


def _close_tickets(db: Session, *criteria, actor: models.User) -> int:
    """Close the matching tickets that are not closed yet. Returns how many.

    The pragma sits on the first line of the statement because that is where
    the AST reports the call, and the guard reads the line a node starts on. A
    ticket's Open/Closed is not an equipment status -- equipment_status_history
    has no row shape for it -- so this bypass is permanent. It is the only one
    in the backend: DATA-H4-2 deleted the two that were waiting for it, and
    DATA-M21 moved this one here so close_ticket and fix_equipment share it.
    """
    return db.query(models.MaintenanceLog).filter(  # audit-trail-bypass: permanent
        *criteria, _not_closed()
    ).update(
        # DATA-H12-2. `.value` rather than the member, and here it is load-
        # bearing rather than defensive: this is a Core UPDATE that bypasses
        # the ORM, so the value goes to the driver as written.
        #
        # The dict key stays the bare string "status" because it is a COLUMN
        # name, not a status value -- and test_audit_trail.py's _names_column
        # guard matches exactly that key, which is why the bypass pragma above
        # has to stay with it.
        #
        # technician_id is the person who closed the ticket (DATA-M21). The
        # column was in the first schema and nothing wrote it before.
        {
            "status": TicketStatus.CLOSED.value,
            "closed_at": clock.utcnow(),
            "technician_id": actor.id,
        },
        synchronize_session=False,
    )

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
        # Pending, whoever reports (SEC-M14). This used to ask may(REPORT_STATUS)
        # over the item's group and let a yes skip review, which handed a
        # company commander's authority over one company's kit a say over the
        # vocabulary every unit shares -- FaultType has no group of its own
        # (see Capability's note on MANAGE_CATALOG). A report's authority is
        # over the item. The fault type it names waits in the approval queue
        # (/catalog, API-H6) for a MANAGE_CATALOG holder other than the
        # reporter, which is setup.approve_fault_type's two-person rule.
        #
        # The report itself is accepted either way, and a pending type is not
        # a dead one: the next report naming it reuses it by name. Only the
        # Report Fault dropdown hides it until someone approves it.
        fault_type = models.FaultType(
            name=report.fault_name,
            is_pending=True,
            requested_by_id=current_user.id
        )
        db.add(fault_type)
        db.commit()
        db.refresh(fault_type)

    # DATA-M21. Below the commit above, which would release it. A close that
    # runs at the same time either sees this ticket or runs after it.
    item = _lock_item(db, item)

    # Create maintenance log
    log = models.MaintenanceLog(
        equipment_id=item.id,
        fault_type_id=fault_type.id,
        description=report.description,
        # DATA-H12-2. The column's own default is this same member
        # (models.MaintenanceLog), so passing it explicitly is redundant and
        # kept rather than deleted: it is the one line that says a reported
        # fault opens a ticket, and reading that off a column default two files
        # away is worse than saying it here.
        status=TicketStatus.OPEN.value
    )
    db.add(log)

    # DATA-H4-2. Both writes sit BELOW the find-or-create db.commit() above and
    # in the same flush as the ticket, which is the whole reason this block is
    # here rather than three lines earlier. Audit a fault above that commit and
    # a later failure leaves a committed history row explaining a ticket that
    # does not exist -- DATA-M4's shape at a new site.
    #
    # The asymmetry between the two calls is deliberate and is the ruling
    # set_status's docstring states: a repeat report on an already-broken item
    # is an EVENT (somebody reported something) and is not a TRANSITION (nothing
    # moved). So the log row is unconditional and the history row is set_status's
    # own no-op rule to decline. The two tables are complete together and
    # neither is complete alone.
    audit_trail.record_event(
        db,
        equipment=item,
        actor=current_user,
        event_type=EventType.FAULT,
    )
    # The description travels into notes because the alternative is a history
    # row reading "Functional -> Malfunctioning, fault_report" with no why, when
    # the why arrived in the same request. It is duplicated onto the ticket
    # above, and that is the lesser cost: the ticket is closed and filtered out
    # by close_ticket or fix_equipment, while this row is the permanent record.
    #
    # `or None` for the same reason fix_equipment uses it below: schemas.py
    # declares description as a bare `str` with no min_length, so "" is a legal
    # request body, and storing it would put a value in the column meaning "the
    # reporter said nothing" that reads as "the reporter said nothing in
    # particular". Two routes writing one column should not disagree about how
    # to spell an absent note.
    audit_trail.set_status(
        db,
        equipment=item,
        actor=current_user,
        new_status=EquipmentStatus.MALFUNCTIONING,
        reason=ChangeReason.FAULT_REPORT,
        notes=report.description or None,
    )

    db.commit()
    return {"status": "Fault Reported", "ticket_id": log.id}

@router.post("/maintenance/fix/{equipment_id}")
def fix_equipment(
    equipment_id: schemas.DbInt,
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
    #
    # DATA-M21. This route repairs the whole ITEM, so it still closes every
    # open ticket of it. A single ticket is closed by close_ticket below,
    # which is what the maintenance page's button calls.
    item = get_scoped_equipment_or_404(db, current_user, equipment_id)
    authz.require(db, current_user.id, Capability.RESOLVE_FAULT, item.group_id)
    item = _lock_item(db, item)

    # DATA-H4-2. This route already logged the FIX below and wrote no history
    # row, so an item could be broken and repaired for years while
    # GET /equipment/{id}/history stayed empty -- the one table that records
    # condition over time was written by create_verification alone.
    #
    # `notes or None` writes NULL today and will keep writing NULL until
    # DATA-M5 lands: `notes` is a QUERY PARAMETER and no client sends one
    # (EquipmentPage.tsx, its one caller, posts with no body), so it is
    # always "". Storing that empty string would put a value in the column that
    # means "the repairer said nothing" and looks like "the repairer said
    # nothing in particular"; NULL says the first honestly.
    audit_trail.set_status(
        db,
        equipment=item,
        actor=current_user,
        new_status=EquipmentStatus.FUNCTIONAL,
        reason=ChangeReason.REPAIR,
        notes=notes or None,
    )

    # Close the item's open tickets.
    _close_tickets(
        db, models.MaintenanceLog.equipment_id == item.id, actor=current_user
    )

    # This site is why record_event normalises user_status_at_time: the other
    # three transaction-log writes recorded it and this one silently did not,
    # so the column answered "was the actor on duty" for three event types out
    # of four. It carries it now, like everything else.
    audit_trail.record_event(
        db,
        equipment=item,
        actor=current_user,
        event_type=EventType.FIX,
    )

    db.commit()
    return {"status": "Fixed", "notes": notes}


@router.post(
    "/maintenance/tickets/{ticket_id}/close", response_model=schemas.TicketResponse
)
def close_ticket(
    ticket_id: schemas.DbInt,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """Close one ticket. DATA-M21.

    The maintenance page's button used to post fix_equipment with the
    ticket's item, which closed every open ticket of that item and declared
    it repaired. This closes the one ticket. The item turns Functional only
    when no other ticket of it is still open, with the same REPAIR history row
    and FIX event fix_equipment writes. Closing one of several writes neither:
    nothing about the item has changed.
    """
    # Resolve, then gate, as fix_equipment does. A ticket is as visible as its
    # item, so an id outside the caller's sight answers the same 404 as one
    # that was never issued.
    ticket = scope_equipment_derived_query(
        db.query(models.MaintenanceLog).filter(models.MaintenanceLog.id == ticket_id),
        models.MaintenanceLog,
        current_user,
    ).first()
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    item = ticket.equipment
    authz.require(db, current_user.id, Capability.RESOLVE_FAULT, item.group_id)
    item = _lock_item(db, item)

    # Read again under the lock: a close that ran while this request waited
    # for it has already closed the ticket. A column query, so the identity
    # map's copy of the ticket cannot answer instead of the database.
    status = (
        db.query(models.MaintenanceLog.status)
        .filter(models.MaintenanceLog.id == ticket.id)
        .scalar()
    )
    if status == TicketStatus.CLOSED.value:
        raise HTTPException(status_code=409, detail="Ticket already closed")

    _close_tickets(db, models.MaintenanceLog.id == ticket.id, actor=current_user)

    still_open = (
        db.query(func.count(models.MaintenanceLog.id))
        .filter(models.MaintenanceLog.equipment_id == item.id, _not_closed())
        .scalar()
    )
    if still_open == 0:
        audit_trail.set_status(
            db,
            equipment=item,
            actor=current_user,
            new_status=EquipmentStatus.FUNCTIONAL,
            reason=ChangeReason.REPAIR,
            notes=None,
        )
        audit_trail.record_event(
            db,
            equipment=item,
            actor=current_user,
            event_type=EventType.FIX,
        )

    db.commit()
    db.refresh(ticket)
    return _ticket_response(ticket)
