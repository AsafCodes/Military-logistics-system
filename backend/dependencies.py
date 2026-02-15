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

def verify_admin_access(user: models.User):
    if user.role != models.UserRole.MASTER and user.role != "master":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Permission denied. Only MASTER can perform this action."
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
