from datetime import timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext

import os
import secrets
from dotenv import load_dotenv

from . import clock

load_dotenv()

# --- Configuration ---
# In a real production app, retrieving these from environment variables is crucial.
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("No SECRET_KEY set for FastAPI application") 
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# --- Session Cookie (SEC-H9) ---
# The token used to live in the browser's localStorage, where any script on the
# page could read it. It now rides in a cookie the page cannot read at all.
COOKIE_NAME = "access_token"

def _cookie_secure_from_env(raw: Optional[str]) -> bool:
    """Fail SECURE: anything but an explicit denial means HTTPS-only.

    An unset variable yields True. That is deliberately the opposite of
    database.py's silent downgrade to a local sqlite file (DATA-H11) -- a
    deployment that forgets this setting should lose its sessions loudly, not
    its transport security quietly. Local http development opts out by name.

    Split out from the constant below so the default is pinned by a test rather
    than asserted by this comment.
    """
    return (raw or "").strip().lower() not in ("0", "false", "no")


COOKIE_SECURE = _cookie_secure_from_env(os.getenv("COOKIE_SECURE"))

# Lax, not Strict: Strict withholds the cookie on inbound top-level navigation,
# so following a link into the app would land on the login page despite a live
# session. Lax still refuses cross-site POSTs, which is the CSRF case that
# matters now that the browser attaches credentials on its own.
COOKIE_SAMESITE = "lax"


def _cookie_attributes():
    """The attribute set shared by setting and clearing the session cookie.

    Not tidiness. A browser matches Set-Cookie deletions on name + path +
    domain, so a delete_cookie whose attributes disagree with the original
    silently does nothing: the user is told they logged out while their browser
    keeps presenting a valid credential until it expires. One definition, two
    call sites, nothing to drift.
    """
    return {
        "httponly": True,
        "secure": COOKIE_SECURE,
        "samesite": COOKIE_SAMESITE,
        "path": "/",
    }


def set_auth_cookie(response, token: str):
    """Attach the session token to `response` as a cookie script cannot read."""
    # Derived from the token's own lifetime rather than restated. SEC-M5 already
    # documents two expiry constants in this file that disagree; a third one
    # would let the cookie outlive the JWT it carries (a session that looks live
    # and 401s on every request) or die first (a silent logout mid-shift).
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        **_cookie_attributes(),
    )


def clear_auth_cookie(response):
    """Remove the session cookie. Must mirror set_auth_cookie exactly."""
    response.delete_cookie(key=COOKIE_NAME, **_cookie_attributes())

# --- Password Hashing ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

# ~144 bits of entropy, comfortably under bcrypt's 72-byte input limit.
PASSWORD_BYTES = 18

def generate_password():
    """A random password for an account this system creates on the operator's behalf."""
    return secrets.token_urlsafe(PASSWORD_BYTES)

# --- JWT Token ---
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = clock.utcnow() + expires_delta
    else:
        expire = clock.utcnow() + timedelta(minutes=15)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt
