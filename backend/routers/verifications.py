"""
Equipment Verification & Status History Router
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..dependencies import get_current_user

router = APIRouter(prefix="/verifications", tags=["Verifications"])


@router.post("/", response_model=schemas.VerificationResponse)
async def create_verification(
    data: schemas.VerificationCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Create a verification record. Updates equipment status if changed."""
    equipment = (
        db.query(models.Equipment)
        .filter(models.Equipment.id == data.equipment_id)
        .first()
    )

    if not equipment:
        raise HTTPException(status_code=404, detail="Equipment not found")

    verification = models.Verification(
        equipment_id=data.equipment_id,
        verification_type=data.verification_type,
        reported_status=data.reported_status,
        findings=data.findings,
        action_required=data.action_required,
        created_by=current_user.id,
    )
    db.add(verification)
    db.flush()

    old_status = equipment.status
    if data.reported_status != old_status:
        equipment.status = data.reported_status
        history = models.EquipmentStatusHistory(
            equipment_id=data.equipment_id,
            old_status=old_status,
            new_status=data.reported_status,
            change_reason="verification",
            verification_id=verification.id,
            notes=data.findings,
            created_by=current_user.id,
        )
        db.add(history)

    equipment.last_verified_at = datetime.utcnow()
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
        reporter_name=current_user.full_name,
    )


@router.get(
    "/equipment/{equipment_id}", response_model=List[schemas.VerificationResponse]
)
async def get_equipment_verifications(
    equipment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get all verifications for a specific equipment."""
    verifications = (
        db.query(models.Verification)
        .filter(models.Verification.equipment_id == equipment_id)
        .order_by(models.Verification.created_date.desc())
        .all()
    )

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
            reporter_name=v.reporter.full_name if v.reporter else None,
        )
        for v in verifications
    ]


history_router = APIRouter(prefix="/equipment", tags=["Equipment History"])


@history_router.get(
    "/{equipment_id}/history", response_model=List[schemas.StatusHistoryResponse]
)
async def get_equipment_status_history(
    equipment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Get status change history for a specific equipment."""
    history = (
        db.query(models.EquipmentStatusHistory)
        .filter(models.EquipmentStatusHistory.equipment_id == equipment_id)
        .order_by(models.EquipmentStatusHistory.created_date.desc())
        .all()
    )

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
            user_name=h.user.full_name if h.user else None,
        )
        for h in history
    ]
