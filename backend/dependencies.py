"""
Authentication and Authorization Dependencies
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session, joinedload
from jose import JWTError, jwt
from datetime import datetime, timedelta
from typing import Optional

from .database import get_db
from . import models
from . import schemas
from . import security

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

SECRET_KEY = security.SECRET_KEY
ALGORITHM = security.ALGORITHM
verify_password = security.verify_password
get_password_hash = security.get_password_hash
create_access_token = security.create_access_token

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = schemas.TokenData(personal_number=username)
    except JWTError:
        raise credentials_exception
    
    user = db.query(models.User).options(joinedload(models.User.profile)).filter(
        models.User.personal_number == token_data.personal_number
    ).first()
    if user is None:
        raise credentials_exception
    return user

async def get_current_active_user(current_user: models.User = Depends(get_current_user)):
    if not current_user.is_active_duty:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

def is_master(user: models.User) -> bool:
    """MASTER is the only role that bypasses hierarchical scoping."""
    return user.role == models.UserRole.MASTER


def verify_admin_access(user: models.User):
    if not is_master(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Permission denied. Only MASTER can perform this action."
        )

def scope_equipment_query(q, user: models.User):
    """Restrict an Equipment query to what `user` is allowed to see.

    Single source of truth for hierarchical data scoping — used by the
    equipment listing and by every per-item access check.
    """
    profile = user.profile
    if is_master(user) or (profile and profile.can_view_all_equipment):
        return q

    own_only = q.filter(models.Equipment.holder_user_id == user.id)
    user_hierarchy = user.unit_hierarchy or user.unit_path
    if not (profile and user_hierarchy):
        return own_only

    if profile.can_view_battalion_realtime:
        # Battalion sits two segments down the materialised path: "188/53/A" -> "188/53".
        prefix = "/".join(user_hierarchy.split("/")[:2])
    elif profile.can_view_company_realtime:
        prefix = user_hierarchy
    else:
        return own_only

    return q.filter(models.Equipment.unit_hierarchy.startswith(prefix))


def get_scoped_equipment_or_404(db: Session, user: models.User, equipment_id: int) -> models.Equipment:
    """Resolve one equipment item within the user's scope.

    Returns 404 rather than 403 for out-of-scope items so that IDs cannot be
    enumerated to discover the existence of assets in other units.

    An item the user holds is always in scope. The listing scope replaces the
    holder filter with a hierarchy filter, so a held item whose unit_hierarchy
    is NULL or outside the user's path would otherwise be unreachable by the
    person actually carrying it.
    """
    q = db.query(models.Equipment).filter(models.Equipment.id == equipment_id)
    item = scope_equipment_query(q, user).first() or q.filter(
        models.Equipment.holder_user_id == user.id
    ).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Equipment not found")
    return item


def verify_can_report_status(user: models.User, item: models.Equipment) -> None:
    """A status write requires holding the item, or administering it."""
    if item.holder_user_id == user.id:
        return
    if is_master(user):
        return
    if user.profile and user.profile.can_change_maintenance_status:
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Permission denied. You must hold this equipment or have maintenance authority.",
    )


def get_daily_status(last_verified_at: Optional[datetime]) -> str:
    now_utc = datetime.utcnow()
    if not last_verified_at:
        return "SEVERE"
    diff = now_utc - last_verified_at
    if diff < timedelta(hours=24):
        return "GOOD"
    elif diff < timedelta(hours=48):
        return "WARNING"
    else:
        return "SEVERE"
