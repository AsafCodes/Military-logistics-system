from datetime import timedelta
from typing import Optional
import bcrypt
from jose import JWTError, jwt

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
# INF-H5. bcrypt is called directly. passlib used to sit between. Its last
# release, 1.7.4, can neither hash nor verify on bcrypt 5.0.0, and
# requirements.txt held bcrypt at 3.2.2 beneath it. The stored format is
# unchanged: "$2b$", cost 12, 60 characters. A hash passlib wrote verifies
# here.

# bcrypt reads the first 72 bytes of a password and no more. 3.2.2 dropped the
# rest without saying so; 5.0.0 raises ValueError instead. Neither the login
# form nor schemas.UserCreate limits a password's length, so the cut is made
# here, on both paths: a longer password is still accepted at login, and an
# account whose password was already longer than this still verifies.
#
# passlib did refuse a password over 4096 characters, by raising. That limit went
# with it, and nothing replaces it here (SEC-M7).
BCRYPT_MAX_PASSWORD_BYTES = 72

def _password_bytes(password):
    if isinstance(password, str):
        password = password.encode("utf-8")
    return password[:BCRYPT_MAX_PASSWORD_BYTES]

def verify_password(plain_password, hashed_password):
    # users.password_hash is nullable; an account without a hash has no
    # password that matches.
    if not hashed_password:
        return False
    password = _password_bytes(plain_password)
    try:
        if isinstance(hashed_password, str):
            hashed_password = hashed_password.encode("utf-8")
        return bcrypt.checkpw(password, hashed_password)
    except ValueError:
        # The stored value is not a hash bcrypt accepts. For a value that is
        # no hash at all passlib raised, and nothing between here and the
        # client catches that. The answer here is False.
        #
        # passlib accepted two kinds of value that bcrypt 5.0.0 does not: the
        # "$2$" prefix, and a salt or checksum whose last character carries
        # bits the encoding leaves unused. The prefix and the salt arrive
        # here; such a checksum is answered False by checkpw itself. Neither
        # passlib as it was set up here nor bcrypt writes any of them, so no
        # hash this application made is among them. One brought in from
        # another system could be, and would stop verifying.
        return False

def get_password_hash(password):
    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt()).decode("ascii")

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
