"""Authentication Router - Login and logout endpoints"""
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import timedelta

from ..database import get_db
from .. import models
from .. import schemas
from .. import security

router = APIRouter(tags=["auth"])

@router.post("/login", response_model=schemas.Token)
def login_for_access_token(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
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

    # SEC-H9. The browser now holds the session in an httpOnly cookie it cannot
    # read, so an XSS on this application can no longer walk off with a bearer
    # token for a military logistics system.
    security.set_auth_cookie(response, access_token)

    # The token stays in the body ON PURPOSE, and deleting it will break things.
    # The body was never the defect -- persisting it in localStorage was, and a
    # login response is readable only by the page that asked for it. Keeping it
    # is what lets dependencies.py's header fallback stay exercised: the whole
    # pytest suite and Swagger's Authorize button authenticate this way.
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response):
    """Clear the session cookie.

    Deliberately unauthenticated, which is a claim worth defending given SEC-C2.
    Gating this on a valid credential inverts the failure: the caller whose
    cookie has expired or been tampered with is exactly the one who needs it
    cleared, and they would get a 401 and keep the cookie. It reads nothing and
    touches no database; its entire effect is one Set-Cookie header.

    KNOWN, ACCEPTED: this makes logout forgeable cross-site. An attacker page
    can auto-submit a form here as a top-level navigation, and the browser
    honours the deleting Set-Cookie even though SameSite=Lax withheld the
    request cookie -- so a third party can sign an operator out mid-shift.
    Availability only: nothing is read, nothing is written, and no session is
    created. Closing it needs a CSRF token, which this application has nowhere
    to put yet; it belongs with the security middleware SEC-M10 tracks.
    """
    security.clear_auth_cookie(response)
