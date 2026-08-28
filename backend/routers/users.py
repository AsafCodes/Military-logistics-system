"""Users Router - User management endpoints"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional

from ..database import get_db
from ..dependencies import get_current_active_user, get_daily_status, scope_user_query
from ..enums import Capability
from .. import authz
from .. import models
from .. import schemas
from .. import security

router = APIRouter(tags=["users"])

@router.post("/users/", response_model=schemas.UserResponse)
def create_user(user: schemas.UserCreate, current_user: models.User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    # verify_admin_access was `user.role == MASTER`, and it is gone along with
    # is_master itself. MANAGE_PERSONNEL comes from a grant that can be
    # revoked and audited, rather than a string on the caller's own row.
    #
    # require_global, not require: the personnel table belongs to no unit. No
    # resolver runs first and none should -- nothing is addressed by id here,
    # so a 403 confirms nothing about any resource.
    authz.require_global(db, current_user.id, Capability.MANAGE_PERSONNEL)

    if db.query(models.User).filter(models.User.personal_number == user.personal_number).first():
        raise HTTPException(status_code=400, detail="User already exists")

    group = db.query(authz.Group).filter(authz.Group.id == user.group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    new_user = models.User(
        personal_number=user.personal_number,
        full_name=user.full_name,
        password_hash=security.get_password_hash(user.password),
        is_active_duty=user.is_active_duty,
    )
    db.add(new_user)
    db.flush()
    db.add(authz.GroupMembership(user_id=new_user.id, group_id=group.id))
    db.commit()
    db.refresh(new_user)
    return new_user

@router.put("/users/{user_id}/group", response_model=schemas.UserResponse)
def update_user_group(user_id: int, req: schemas.UpdateUserGroupRequest, current_user: models.User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    # Reassigning where someone sits is a personnel act, gated the same as
    # creating them -- H1-12 replaces the old profile-assignment route with
    # this one, same MANAGE_PERSONNEL gate, same replace-not-add shape.
    authz.require_global(db, current_user.id, Capability.MANAGE_PERSONNEL)

    target_user = db.query(models.User).filter(models.User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="Target user not found")

    group = db.query(authz.Group).filter(authz.Group.id == req.group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    db.query(authz.GroupMembership).filter(
        authz.GroupMembership.user_id == user_id
    ).delete()
    db.add(authz.GroupMembership(user_id=user_id, group_id=group.id))
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
    # SEC-H5, and the leak H1-10 deferred here by name: this route had no gate
    # of ANY kind, so a private read the full roster -- personal_number is the
    # military ID -- along with every account's permission matrix.
    #
    # Scoped rather than gated on MANAGE_PERSONNEL. The two write routes in
    # this file are administrative acts and belong to that verb; a roster is a
    # listing, and every other listing in the system answers "what may you
    # see" rather than "are you an administrator". A company commander should
    # see their company without being able to create users.
    query = scope_user_query(
        db,
        db.query(models.User).options(
            joinedload(models.User.memberships).joinedload(authz.GroupMembership.group)
        ),
        current_user,
    )
    if q:
        query = query.filter((models.User.full_name.ilike(f"%{q}%")) | (models.User.personal_number.ilike(f"%{q}%")))
    else:
        query = query.limit(50)
    return query.all()
