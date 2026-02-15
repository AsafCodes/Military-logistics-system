"""Users Router - User management endpoints"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional

from ..database import get_db
from ..dependencies import get_current_active_user, verify_admin_access, get_daily_status
from .. import models
from .. import schemas
from .. import security

router = APIRouter(tags=["users"])

@router.post("/users/", response_model=schemas.UserResponse)
def create_user(user: schemas.UserCreate, db: Session = Depends(get_db)):
    if db.query(models.User).filter(models.User.personal_number == user.personal_number).first():
        raise HTTPException(status_code=400, detail="User already exists")

    user_count = db.query(models.User).count()
    assigned_role = "master" if user_count == 0 else "user"
    target_profile_name = "Master" if user_count == 0 else "Soldier"
        
    profile = db.query(models.Profile).filter(models.Profile.name == target_profile_name).first()
    profile_id = profile.id if profile else None

    new_user = models.User(
        personal_number=user.personal_number, 
        full_name=user.full_name, 
        battalion=user.battalion, 
        company=user.company, 
        password_hash=security.get_password_hash(user.password),
        is_active_duty=user.is_active_duty,
        role=assigned_role,
        profile_id=profile_id
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@router.put("/users/promote", response_model=schemas.UserResponse)
def promote_user(req: schemas.PromoteUserRequest, current_user: models.User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    verify_admin_access(current_user)
    target_user = db.query(models.User).filter(models.User.id == req.target_user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="Target user not found")
    target_user.role = req.new_role
    db.commit()
    db.refresh(target_user)
    return target_user

@router.get("/users/me/equipment", response_model=List[schemas.EquipmentResponse])
def get_my_equipment(current_user: models.User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    items = db.query(models.Equipment).filter(models.Equipment.holder_user_id == current_user.id).order_by(models.Equipment.id.asc()).all()
    return [schemas.EquipmentResponse(
        id=item.id, type=item.item_name, item_name=item.item_name, status=item.status,
        current_state_description=item.current_state_description, compliance_check=item.report_status,
        report_status=item.report_status, compliance_level=get_daily_status(item.last_verified_at),
        holder_user_id=item.holder_user_id, custom_location=item.custom_location,
        actual_location_id=item.actual_location_id, serial_number=item.serial_number
    ) for item in items]

@router.get("/users/me", response_model=schemas.UserResponse)
def read_users_me(current_user: models.User = Depends(get_current_active_user)):
    return current_user

@router.get("/users", response_model=List[schemas.UserResponse])
def list_all_users(q: Optional[str] = None, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_active_user)):
    query = db.query(models.User).options(joinedload(models.User.profile))
    if q:
        query = query.filter((models.User.full_name.ilike(f"%{q}%")) | (models.User.personal_number.ilike(f"%{q}%")))
    else:
        query = query.limit(50)
    return query.all()
