"""
Equipment Verification & Status History Router
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload
from typing import List

from ..database import get_db
from .. import audit_trail, authz, models, schemas
from ..enums import Capability, ChangeReason, EquipmentStatus, EventType
from ..dependencies import (
    get_current_active_user,
    get_scoped_equipment_or_404,
    require_status_authority,
)

router = APIRouter(prefix="/verifications", tags=["Verifications"])


@router.post("/", response_model=schemas.VerificationResponse)
async def create_verification(
    data: schemas.VerificationCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """Create a verification record. Updates equipment status if changed."""
    equipment = get_scoped_equipment_or_404(db, current_user, data.equipment_id)
    require_status_authority(db, current_user, equipment)

    reported_status = data.reported_status.value

    # Declaring a broken item serviceable is closing a fault, whichever route
    # says it, so it asks the verb that closes faults.
    #
    # require_status_authority is possession-OR-REPORT_STATUS, and its own
    # docstring states the invariant this route was breaking: "a soldier
    # holding a broken item can report it and cannot declare it fixed. That
    # asymmetry is the whole reason the two verbs exist." It was enforced only
    # by which routes call which helper -- and this route wrote the caller's
    # reported_status straight onto equipment.status through set_status, so
    # POST /verifications/ with "Functional" did exactly what
    # maintenance.fix_equipment refuses to do without RESOLVE_FAULT. Verified
    # against the fixtures before fixing: grant-less soldier_a, holding SA100,
    # got 403 from POST /maintenance/fix/{id} and 200 from this route.
    #
    # WHAT THIS DOES NOT FIX, stated plainly because the gate invites the
    # opposite assumption: fix_equipment also closes the item's open
    # MaintenanceLog rows and this route still does not, so a verification that
    # declares an item Functional leaves its ticket Open -- readiness
    # (analytics counts status == "Functional") then disagrees with the fault
    # list. This change decides WHO may reach that state, not whether it
    # exists, and a RESOLVE_FAULT holder still reaches it here. The mirror gap
    # is open too: this is the only route that writes Malfunctioning, and it
    # opens no ticket, where maintenance.report_fault always does. Both want
    # one shared status-transition helper owning the ticket side-effect, which
    # is a larger change than this ticket and is not smuggled into it.
    #
    # ON THE TRANSITION, not on the value. An item already Functional that is
    # verified as Functional closes no fault; that is the ordinary condition
    # report this route exists for, and set_status no-ops on it anyway.
    # Gating the value rather than the move would demand RESOLVE_FAULT for
    # every routine check of a working item, which is the possession arm's
    # entire purpose.
    #
    # Refuses the whole request rather than writing the verification and
    # silently declining the status change: a stored report whose reported
    # status the system did not act on is a record that lies about what
    # happened. Raised BEFORE any write, so a refused report leaves nothing
    # behind -- no verification row, no clock advance.
    declares_serviceable = (
        reported_status == EquipmentStatus.FUNCTIONAL.value
        and equipment.status != EquipmentStatus.FUNCTIONAL.value
    )
    if declares_serviceable:
        authz.require(
            db, current_user.id, Capability.RESOLVE_FAULT, equipment.group_id
        )

    # equipment.id, not data.equipment_id, at both writes below. They are the
    # same value today and only because the resolver filtered on it -- taking
    # it from the resolved row is what keeps that a fact rather than a
    # coincidence two edits from now.
    verification = models.Verification(
        equipment_id=equipment.id,
        verification_type=data.verification_type,
        reported_status=reported_status,
        findings=data.findings,
        action_required=data.action_required,
        created_by=current_user.id
    )
    db.add(verification)
    db.flush()
    
    # DATA-H4-3. UNCONDITIONAL, and it sits beside a call that is not -- which
    # is the whole point of the pair. An inspection happened, so this row is
    # written; a transition may not have, so set_status below decides for
    # itself.
    #
    # Before this, a verification that CONFIRMED the existing status wrote
    # nothing into either audit table: set_status no-ops when nothing moved and
    # nothing else recorded that anyone had looked. Somebody laid eyes on a
    # rifle, filed a report, and the audit trail said no.
    #
    # CONDITION_REPORT, not VERIFICATION. That string means
    # equipment.verify_equipment_daily -- the daily presence confirmation, gated
    # on possession alone -- and this route is a condition report gated on
    # require_status_authority. Two acts, two gates, two values.
    #
    # DATA-H5. The same call advances last_verified_at, so the clock and the
    # row proving an inspection happened cannot be written apart.
    audit_trail.set_last_verified_at(
        db,
        equipment=equipment,
        actor=current_user,
        event_type=EventType.CONDITION_REPORT,
    )

    # The flush above is what makes verification_id available here, and it is
    # the reason it cannot move. audit_trail.set_status owns both halves of the
    # change now -- the assignment and the row -- so the "did the status
    # actually move" question is asked once, there, rather than at each caller.
    audit_trail.set_status(
        db,
        equipment=equipment,
        actor=current_user,
        new_status=reported_status,
        reason=ChangeReason.VERIFICATION,
        notes=data.findings,
        verification_id=verification.id,
    )

    db.commit()
    db.refresh(verification)
    
    return schemas.VerificationResponse(
        id=verification.id,
        equipment_id=verification.equipment_id,
        verification_type=verification.verification_type,
        reported_status=verification.reported_status,
        findings=verification.findings,
        action_required=verification.action_required,
        created_date=verification.created_date,
        created_by=verification.created_by,
        reporter_name=current_user.full_name
    )


@router.get("/equipment/{equipment_id}", response_model=List[schemas.VerificationResponse])
async def get_equipment_verifications(
    equipment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """Get all verifications for a specific equipment."""
    # Resolve before reading. The filter below took the raw path parameter, so
    # the observation history of any asset in the force -- who checked it, when,
    # what they found -- was readable by any authenticated user who could count.
    # That is SEC-H6's read half, at a route nothing else guarded.
    #
    # No require() follows, and the omission is deliberate rather than an
    # oversight: this is a read, and the resolver IS the VIEW gate. Adding a
    # verb here would demand authority to see history for an item the caller can
    # already see, list, and hold.
    item = get_scoped_equipment_or_404(db, current_user, equipment_id)

    # DATA-H8. reporter_name below reads v.reporter.full_name, so an item with a
    # long inspection history cost one SELECT per verification -- and the rows
    # are worst-case for the identity map, since a different person files each
    # one, so nothing is cached between iterations.
    #
    # Inline rather than through dependencies.EQUIPMENT_RESPONSE_LOADS: that
    # tuple is about Equipment's response properties and this is a single
    # relationship on a different model. A shared name covering both would have
    # to mean "whatever the loop happens to touch", which is not a thing that
    # can be kept honest.
    verifications = db.query(models.Verification).options(
        joinedload(models.Verification.reporter)
    ).filter(
        models.Verification.equipment_id == item.id
    ).order_by(models.Verification.created_date.desc()).all()
    
    return [
        schemas.VerificationResponse(
            id=v.id,
            equipment_id=v.equipment_id,
            verification_type=v.verification_type,
            reported_status=v.reported_status,
            findings=v.findings,
            action_required=v.action_required,
            created_date=v.created_date,
            created_by=v.created_by,
            reporter_name=v.reporter.full_name if v.reporter else None
        ) for v in verifications
    ]


history_router = APIRouter(prefix="/equipment", tags=["Equipment History"])


@history_router.get("/{equipment_id}/history", response_model=List[schemas.StatusHistoryResponse])
async def get_equipment_status_history(
    equipment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """Get status change history for a specific equipment."""
    # Same treatment, same reason as the verification list above: the raw path
    # parameter leaked every status change an asset had ever undergone, with the
    # user who made each one named.
    item = get_scoped_equipment_or_404(db, current_user, equipment_id)

    # DATA-H8, the sibling of the load above and for the same reason: user_name
    # below reads h.user.full_name once per row, and a status history is exactly
    # the table that grows without bound on a well-used item.
    history = db.query(models.EquipmentStatusHistory).options(
        joinedload(models.EquipmentStatusHistory.user)
    ).filter(
        models.EquipmentStatusHistory.equipment_id == item.id
    ).order_by(models.EquipmentStatusHistory.created_date.desc()).all()
    
    return [
        schemas.StatusHistoryResponse(
            id=h.id,
            equipment_id=h.equipment_id,
            old_status=h.old_status,
            new_status=h.new_status,
            change_reason=h.change_reason,
            verification_id=h.verification_id,
            notes=h.notes,
            created_date=h.created_date,
            created_by=h.created_by,
            user_name=h.user.full_name if h.user else None
        ) for h in history
    ]
