"""Authentication Router - Login endpoint"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import timedelta

from ..database import get_db
from .. import models
from .. import schemas
from .. import security

router = APIRouter(tags=["auth"])

@router.post("/login", response_model=schemas.Token)
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.personal_number == form_data.username).first()
    if not user or not security.verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # SEC-H8's second half, and the half that actually matters. Refusing an
    # inactive account per-route still ISSUES it a token, so every route that
    # forgets get_current_active_user is a hole -- and two verification reads
    # were exactly that until this entry. Refuse at the door instead, so a
    # discharged account never holds a credential at all.
    #
    # Deliberately the same 401 and the same wording as the branch above. A
    # distinct code or message here would turn the login form into an oracle
    # for which personal numbers exist and have been deactivated (cf. SEC-M4,
    # the timing oracle, still open).
    if not user.is_active_duty:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=security.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = security.create_access_token(
        data={"sub": user.personal_number}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}
