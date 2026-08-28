"""SEC-H9: the session token rides in an httpOnly cookie, not localStorage.

Three questions this file exists to keep answered:

  1. Does login hand the browser a cookie script cannot read, with attributes
     that actually constrain it?
  2. Does the Authorization header still work? Every other module in this suite
     authenticates that way, and Swagger's Authorize button does too. If the
     fallback in dependencies.OAuth2PasswordBearerWithCookie ever goes away,
     306 tests break and the reason will not be obvious from any one of them.
  3. Does logout actually delete the cookie? A delete_cookie whose attributes
     disagree with the original is a no-op in a real browser, and the failure
     is silent in the worst direction: the user is told they are logged out
     while their browser keeps presenting a live credential.
"""
from datetime import timedelta

import pytest
from conftest import create_auth_header

from backend import security

LOGIN_FORM = {"username": "u_master", "password": "secret"}


def _set_cookie_header(response):
    """The raw Set-Cookie line, which is where the attributes live.

    response.cookies parses the value and throws the attributes away, so a test
    that reads it can assert the token arrived while saying nothing at all about
    whether it is httpOnly -- which is the entire claim of this ticket.
    """
    headers = response.headers.get_list("set-cookie")
    assert headers, "response carried no Set-Cookie header"
    return next(h for h in headers if h.startswith(f"{security.COOKIE_NAME}="))


# --- Login issues a cookie -------------------------------------------------

def test_login_sets_httponly_cookie(client, mock_matrix_db):
    response = client.post("/login", data=LOGIN_FORM)
    assert response.status_code == 200

    raw = _set_cookie_header(response)
    lowered = raw.lower()

    # The whole point: no script on the page can read this.
    assert "httponly" in lowered
    assert "samesite=lax" in lowered
    assert "path=/" in lowered
    assert client.cookies.get(security.COOKIE_NAME)


def test_cookie_lifetime_matches_the_token_lifetime(client, mock_matrix_db):
    """No third expiry constant.

    SEC-M5 already documents two lifetimes in security.py that disagree. A
    cookie outliving its JWT is a session that looks live and 401s on every
    request; one dying first is a silent logout mid-shift.
    """
    raw = _set_cookie_header(client.post("/login", data=LOGIN_FORM))
    expected = security.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    assert f"Max-Age={expected}" in raw


def test_login_body_still_carries_the_token(client, mock_matrix_db):
    """Non-browser clients have no other way in.

    Pinned because removing it looks like a tidy-up -- "the cookie is the
    session now" -- and it would break this entire suite plus Swagger.
    """
    body = client.post("/login", data=LOGIN_FORM).json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"


def test_secure_attribute_is_emitted_when_configured(client, mock_matrix_db, monkeypatch):
    """conftest downgrades COOKIE_SECURE for http testserver; production does not."""
    monkeypatch.setattr(security, "COOKIE_SECURE", True)
    raw = _set_cookie_header(client.post("/login", data=LOGIN_FORM))
    assert "Secure" in raw


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, True),      # unset must NOT downgrade -- the whole point
        ("", True),        # neither may an empty assignment
        ("true", True),
        ("anything", True),
        ("false", False),
        ("FALSE", False),
        (" false ", False),
        ("0", False),
        ("no", False),
    ],
)
def test_cookie_secure_defaults_to_secure(raw, expected):
    assert security._cookie_secure_from_env(raw) is expected


# --- Authenticating with each credential form ------------------------------

def test_cookie_alone_authenticates(client, mock_matrix_db):
    client.post("/login", data=LOGIN_FORM)
    # No Authorization header anywhere -- the jar supplies the cookie.
    response = client.get("/users/me")
    assert response.status_code == 200
    assert response.json()["personal_number"] == "u_master"


def test_header_alone_still_authenticates(client, mock_matrix_db):
    """The fallback the other 306 tests depend on, asserted where it is visible."""
    client.cookies.clear()
    response = client.get("/users/me", headers=create_auth_header("u_master"))
    assert response.status_code == 200
    assert response.json()["personal_number"] == "u_master"


def test_explicit_header_beats_ambient_cookie(client, mock_matrix_db):
    """Precedence is a decision, so it gets a test rather than an accident.

    Two credentials for two DIFFERENT people, so the winner is identifiable
    rather than merely 'a 200'.
    """
    client.post("/login", data={"username": "u_soldier_a", "password": "secret"})
    response = client.get("/users/me", headers=create_auth_header("u_master"))
    assert response.status_code == 200
    assert response.json()["personal_number"] == "u_master"


def test_a_stale_cookie_does_not_shadow_a_valid_header(client, mock_matrix_db):
    """The operational trap that decided the precedence above.

    Cookie-first would return the junk cookie unvalidated and 401 the request,
    with nothing pointing at the browser's leftover cookie as the cause. This is
    the Swagger-after-a-key-rotation case, and the reason tests here that clear
    the jar are not just being tidy.
    """
    client.cookies.set(security.COOKIE_NAME, "stale-garbage-from-a-previous-deploy")
    response = client.get("/users/me", headers=create_auth_header("u_master"))
    assert response.status_code == 200
    assert response.json()["personal_number"] == "u_master"


@pytest.mark.parametrize(
    "header",
    [
        "",                     # a gateway that injects the key with no value
        "Basic ZGVtbzpkZW1v",   # a different scheme on the same request
        "Bearer",               # truncated
        "bearer   ",            # scheme only
    ],
    ids=["empty", "basic", "truncated", "scheme-only"],
)
def test_junk_auth_header_does_not_defeat_a_valid_cookie(client, mock_matrix_db, header):
    """Presence of a header is not possession of a credential.

    Written after the first attempt at the precedence rule above tested
    `"authorization" in request.headers` and locked out every one of these --
    a browser with a live session, 401'd because something upstream added a
    header it never asked for. Proxies, gateways and extensions all do this,
    and the failure gives the operator nothing to look at.
    """
    client.post("/login", data=LOGIN_FORM)
    response = client.get("/users/me", headers={"Authorization": header})
    assert response.status_code == 200
    assert response.json()["personal_number"] == "u_master"


def test_junk_auth_header_with_no_cookie_is_still_rejected(client, mock_matrix_db):
    """The other half: falling back to the cookie must not become falling open."""
    client.cookies.clear()
    assert client.get("/users/me", headers={"Authorization": "Basic ZGVtbw=="}).status_code == 401


def test_no_credential_at_all_is_rejected(client, mock_matrix_db):
    client.cookies.clear()
    assert client.get("/users/me").status_code == 401


# --- Hostile and malformed cookies -----------------------------------------

@pytest.mark.parametrize(
    "value",
    [
        "garbage",
        "a.b.c",                    # JWT-shaped, not a JWT
        "",                         # present but empty
        "Bearer " + "x" * 40,       # a client that pasted the header form in
    ],
    ids=["garbage", "jwt-shaped", "empty", "header-form"],
)
def test_malformed_cookie_is_401_not_500(client, mock_matrix_db, value):
    """A tampered cookie must be refused, not crash the request.

    jwt.decode raises on all of these; get_current_user catches JWTError. The
    failure mode being guarded against is a 500 that leaks a stack trace to an
    unauthenticated caller (cf. SEC-M8).
    """
    client.cookies.set(security.COOKIE_NAME, value)
    assert client.get("/users/me").status_code == 401


def test_forged_cookie_is_rejected(client, mock_matrix_db, monkeypatch):
    """A well-formed, unexpired JWT naming a real user -- signed with the wrong key.

    The attack the whole scheme rests on: a cookie is client-side data, so an
    attacker who knows the payload shape can mint one. Only the signature stops
    them.

    Minted through the production helper with the key swapped underneath, rather
    than by hand-rolling a second jwt.encode here -- a hand-rolled token that
    drifted from what the app issues would start passing for the wrong reason.
    Swapping security.SECRET_KEY affects only minting: dependencies.py bound its
    own copy at import, so the verifying side still holds the real key.
    """
    monkeypatch.setattr(security, "SECRET_KEY", "not-the-real-signing-key")
    forged = security.create_access_token(
        data={"sub": "u_master"}, expires_delta=timedelta(minutes=30)
    )
    monkeypatch.undo()

    client.cookies.set(security.COOKIE_NAME, forged)
    assert client.get("/users/me").status_code == 401


def test_cookie_with_no_subject_is_rejected(client, mock_matrix_db):
    """Correctly signed, unexpired, but carrying no identity."""
    valid_but_empty = security.create_access_token(data={}, expires_delta=timedelta(minutes=30))
    client.cookies.set(security.COOKIE_NAME, valid_but_empty)
    assert client.get("/users/me").status_code == 401


def test_expired_cookie_is_rejected(client, mock_matrix_db):
    expired = security.create_access_token(
        data={"sub": "u_master"}, expires_delta=timedelta(minutes=-5)
    )
    client.cookies.set(security.COOKIE_NAME, expired)
    assert client.get("/users/me").status_code == 401


def test_cookie_for_a_deleted_user_is_rejected(client, mock_matrix_db):
    """A well-formed, unexpired token whose subject no longer exists."""
    client.cookies.set(
        security.COOKIE_NAME,
        security.create_access_token(
            data={"sub": "u_does_not_exist"}, expires_delta=timedelta(minutes=30)
        ),
    )
    assert client.get("/users/me").status_code == 401


# --- Logout ----------------------------------------------------------------

def test_logout_clears_the_cookie(client, mock_matrix_db):
    client.post("/login", data=LOGIN_FORM)
    assert client.get("/users/me").status_code == 200

    assert client.post("/logout").status_code == 204
    assert not client.cookies.get(security.COOKIE_NAME)
    assert client.get("/users/me").status_code == 401


def test_logout_attributes_mirror_login(client, mock_matrix_db):
    """The silent-delete-failure guard.

    Browsers match a deletion on name + path (+ domain). If these two headers
    ever stop agreeing, the jar keeps the original cookie and the user stays
    authenticated after clicking Log out -- and every assertion above still
    passes, because httpx is more forgiving than a browser here.
    """
    login = _set_cookie_header(client.post("/login", data=LOGIN_FORM))
    logout = _set_cookie_header(client.post("/logout"))

    def attributes(raw):
        parts = [p.strip().lower() for p in raw.split(";")[1:]]
        # Max-Age/expires necessarily differ -- that is what expiring IS.
        return {p for p in parts if not p.startswith(("max-age", "expires"))}

    assert attributes(login) == attributes(logout)


def test_logout_works_without_a_valid_session(client, mock_matrix_db):
    """Deliberately unauthenticated; see the docstring on routers.auth.logout.

    Gating logout on a valid credential would refuse exactly the caller who
    needs the cookie cleared -- the one holding an expired or tampered one.
    """
    client.cookies.clear()
    assert client.post("/logout").status_code == 204

    client.cookies.set(security.COOKIE_NAME, "garbage")
    assert client.post("/logout").status_code == 204


# --- Login refusals must not leak a credential -----------------------------

@pytest.mark.parametrize(
    "form",
    [
        {"username": "u_master", "password": "wrong"},
        {"username": "u_nobody", "password": "secret"},
    ],
    ids=["bad-password", "unknown-user"],
)
def test_failed_login_sets_no_cookie(client, mock_matrix_db, form):
    response = client.post("/login", data=form)
    assert response.status_code == 401
    assert not response.headers.get_list("set-cookie")
    assert not client.cookies.get(security.COOKIE_NAME)


def test_deactivation_mid_session_returns_400_not_401(client, db_session, mock_matrix_db):
    """Documents existing behaviour that the cookie makes more consequential.

    A live cookie whose owner is deactivated afterwards hits
    get_current_active_user, which raises 400 "Inactive user" rather than 401.
    Both frontend interceptors key on 401, so nothing signs the operator out --
    they sit on a rendered dashboard whose every request fails.

    NOT a regression from this ticket: the status code and the interceptors both
    predate it, and a cold load still bounces correctly because resolveSession
    treats any failure as "no session". Pinned here because SEC-L5 already owns
    the fix, and because a passing suite should not imply this is intended.
    """
    client.post("/login", data={"username": "u_soldier_a", "password": "secret"})
    assert client.get("/users/me").status_code == 200

    user = mock_matrix_db["soldier_a"]
    user.is_active_duty = False
    db_session.commit()

    # Change this to 401 when SEC-L5 lands; the cookie itself is still honoured.
    assert client.get("/users/me").status_code == 400


def test_inactive_user_gets_no_cookie(client, db_session, mock_matrix_db):
    """SEC-H8 refuses inactive accounts at the door. Confirm the door is shut
    before the cookie is written, not after -- an issued credential that is
    merely 'not returned in the body' would still be sitting in the browser.
    """
    user = mock_matrix_db["soldier_a"]
    user.is_active_duty = False
    db_session.commit()

    response = client.post("/login", data={"username": "u_soldier_a", "password": "secret"})
    assert response.status_code == 401
    assert not response.headers.get_list("set-cookie")
    assert not client.cookies.get(security.COOKIE_NAME)


# --- The browser has to be allowed to send it ------------------------------

def test_no_get_route_changes_state(client):
    """The property that makes SameSite=Lax a sufficient CSRF defence.

    Before this ticket the browser attached nothing on its own, so CSRF was
    structurally impossible: an attacker's page could forge a request but not
    the Authorization header. The cookie changes that -- the browser now
    volunteers the credential -- and SameSite=Lax is what replaces the missing
    header as the defence.

    Lax withholds the cookie from cross-site POST/PUT/DELETE but STILL SENDS IT
    on a top-level GET navigation. So the defence holds only while every GET is
    read-only. The moment someone adds `GET /equipment/{id}/delete`, a link in
    an email mutates the fleet, and nothing else in this suite would notice.

    An allowlist rather than a rule, because "does this mutate" is not
    something a test can infer. Adding a GET route here is fine; it just has to
    be a deliberate act rather than an accident.
    """
    read_only_gets = {
        "/",
        "/analytics/unit_readiness",
        "/equipment/accessible",
        "/equipment/{equipment_id}/history",
        "/groups",
        "/reports/daily_movement",
        "/reports/query",
        "/setup/fault_types",
        "/setup/fault_types/pending",
        "/tickets/",
        "/users",
        "/users/me",
        "/users/me/equipment",
        "/verifications/equipment/{equipment_id}",
    }

    spec = client.app.openapi()
    actual = {path for path, ops in spec["paths"].items() if "get" in ops}

    # Compared both ways. Checking only for NEW routes lets a deleted one linger
    # here forever, and a later route reusing that path would then inherit an
    # approval nobody gave it.
    assert actual == read_only_gets, (
        f"GET routes changed. Added: {sorted(actual - read_only_gets) or 'none'}. "
        f"Removed: {sorted(read_only_gets - actual) or 'none'}. Confirm every "
        "added route is READ-ONLY -- a state-changing GET is reachable "
        "cross-site under SameSite=Lax -- then update the list above."
    )


def test_security_scheme_name_is_unchanged(client):
    """Subclassing OAuth2PasswordBearer renames the scheme unless pinned.

    FastAPI keys securitySchemes off the class name, so the subclass silently
    republished the contract that `npm run generate-client` consumes. The
    transport moved into a cookie; the scheme did not change.
    """
    schemes = client.app.openapi()["components"]["securitySchemes"]
    assert "OAuth2PasswordBearer" in schemes
    assert schemes["OAuth2PasswordBearer"]["flows"]["password"]["tokenUrl"] == "login"


def test_cors_still_allows_credentials(client, mock_matrix_db):
    """withCredentials on the frontend is useless without this header.

    An allow_credentials that regressed to False would leave the app broken in
    a browser while every test above still passed, because TestClient does not
    enforce CORS.
    """
    response = client.get(
        "/users/me",
        headers={
            **create_auth_header("u_master"),
            "Origin": "http://localhost:3000",
        },
    )
    assert response.headers["access-control-allow-credentials"] == "true"
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
