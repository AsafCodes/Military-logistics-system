"""
Authentication and Authorization Dependencies
"""
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from fastapi.security.utils import get_authorization_scheme_param
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload
from jose import JWTError, jwt
from datetime import datetime, timedelta
from typing import Optional

from .database import get_db
from .enums import Capability
from . import authz
from . import clock
from . import models
from . import schemas
from . import security

class OAuth2PasswordBearerWithCookie(OAuth2PasswordBearer):
    """Read the session token from the cookie, falling back to the header.

    SEC-H9 moved the browser's copy of the token into an httpOnly cookie. The
    header path is NOT legacy tolerance to be removed later -- it is the only
    way a non-browser client can authenticate at all, and it is what the entire
    pytest suite and Swagger's Authorize button use. Removing it breaks both.

    Subclassed rather than written as a fresh SecurityBase so that tokenUrl,
    the OpenAPI security metadata that drives `npm run generate-client`, and the
    401 + WWW-Authenticate shape all stay exactly as they were.

    An explicit Authorization header WINS over the cookie. The precedence is
    deliberate and pinned by tests/test_cookie_auth.py. Cookie-first is the
    tempting order and it sets an operational trap: this scheme returns the
    cookie unvalidated, so a stale or tampered one shadows a perfectly good
    header, and the request 401s with no hint that the browser's leftover cookie
    is the reason. After a SECRET_KEY rotation a fresh token pasted into Swagger
    fails until the operator thinks to clear their cookies.

    The header cannot arrive by accident -- a caller had to attach it -- while
    the cookie is sent ambiently by the browser on every request. Explicit
    intent beats ambient state. Browsers send no header, so they are unaffected.

    "Wins" means a header CARRYING A USABLE BEARER TOKEN, which is not the same
    as the header being present. Testing for mere presence -- the obvious way to
    write this -- means an empty `Authorization:`, a `Basic` credential, or a
    truncated `Bearer` locks out a browser holding a perfectly good cookie.
    Proxies and gateways inject exactly those, and the resulting 401 would look
    like a broken session with nothing to point at the header.
    """

    async def __call__(self, request: Request) -> Optional[str]:
        # FastAPI's own parser, the one OAuth2PasswordBearer uses internally, so
        # "what counts as a Bearer header" cannot drift from the parent class.
        scheme, param = get_authorization_scheme_param(
            request.headers.get("Authorization", "")
        )
        if scheme.lower() == "bearer" and param:
            return param

        token = request.cookies.get(security.COOKIE_NAME)
        if token:
            return token

        # Nothing usable anywhere. super() raises the 401 with its
        # WWW-Authenticate header, so the framework keeps owning that shape.
        return await super().__call__(request)


# scheme_name pinned to the ORIGINAL class name on purpose. FastAPI derives the
# securitySchemes key in openapi.json from the class, so subclassing silently
# renamed it to "OAuth2PasswordBearerWithCookie" -- a published contract change,
# visible to `npm run generate-client`, in exchange for nothing. The transport
# changed; the scheme did not.
oauth2_scheme = OAuth2PasswordBearerWithCookie(
    tokenUrl="login", scheme_name="OAuth2PasswordBearer"
)

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
    
    user = db.query(models.User).options(
        joinedload(models.User.memberships).joinedload(authz.GroupMembership.group)
    ).filter(
        models.User.personal_number == token_data.personal_number
    ).first()
    if user is None:
        raise credentials_exception
    return user

async def get_current_active_user(current_user: models.User = Depends(get_current_user)):
    if not current_user.is_active_duty:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

def scope_equipment_query(q, user: models.User):
    """Restrict an Equipment query to what `user` is allowed to see.

    Visibility is not a special case of authority — it is may(P, VIEW, R). An
    item is visible when the group it belongs to lies in extent(user, VIEW):
    the union of the subtrees under the user's VIEW grants. Authority is
    positional, so the same grant means "the whole force" on the root group and
    "one company" on a leaf, which is how the profile's ladder of visibility
    booleans collapsed into where a single grant sits.

    The one comparison left is an equality against a bound parameter. That is
    the whole of SEC-H2: the prefix match this replaced compiled to an
    unescaped LIKE, so a sibling battalion "188/5" matched "188/53/A" and a
    stored "%" read the entire force. There is no prefix left to anchor.

    The holder arm is a second, independent reason to see something, not a
    fallback for the unprivileged: you can always see what you are carrying.
    Applying it to everyone is what makes this agree with
    get_scoped_equipment_or_404 below, which has always ORed the holder in —
    before, a commander could resolve an item they held outside their own
    subtree by id but could not find it in any listing.

    Both arms are now total. H1-6 made creation write group_id and H1-11 made
    the column NOT NULL, so the NULL group this used to warn about -- visible
    to its holder and to nobody above them, because `NULL IN (...)` is NULL --
    is no longer a state any row can be in.
    """
    # H1-10 deleted the is_master arm that used to stand here, and with it the
    # last role comparison in any authorization path. MASTER's sight is now the
    # VIEW grant on the root group -- issued by seed_data, by the test fixture
    # and by bootstrap_admin.grant_root_authority alike -- so the bypass was
    # replaced rather than merely removed. Positionally it says the same thing:
    # desc(root) is every group there is.
    #
    # The consequence is asserted rather than assumed, and it is the difference
    # between this entry and H1-8/H1-9. A master stripped of grants used to get
    # 403 -- seeing the whole fleet, able to act on none of it. They now get
    # 404: sight and authority both come from the same place, so there is no
    # longer a state where one outlives the other.
    #
    # The profile's can_view_all_equipment used to sit beside it as a second
    # bypass, and is deliberately gone. Only the "Master" profile sets it, and
    # both places that assign that profile (seed_data.py, bootstrap_admin.py)
    # also set role=MASTER -- so in practice it never decided anything the line
    # below did not. It was reachable, though: PUT /users/{id}/profile lets an
    # admin hand the Master profile to an ordinary user, who then read the whole
    # force on the strength of a boolean with no grant behind it anywhere. That
    # is the conflation of role and authority this model exists to end, so the
    # narrowing is the point rather than a side effect. Such a user now sees
    # exactly what their grants say.
    return q.filter(or_(
        models.Equipment.group_id.in_(authz.extent(user.id, Capability.VIEW)),
        models.Equipment.holder_user_id == user.id,
    ))


def scope_equipment_derived_query(q, model, user: models.User):
    """Restrict a query over rows that REFER to equipment.

    Transaction logs and maintenance tickets are not scoped in their own
    right: each is exactly as visible as the item it describes. Both routes
    wrote that as the same join followed by the same scope call, which is one
    predicate in two places -- the DATA-H9 shape, and an odd one to leave in
    the entry closing SEC-H5, whose whole complaint was scoping written
    per-endpoint instead of once.

    The join is INNER, deliberately. A row whose equipment_id is NULL or
    dangling describes nothing, so there is no scope under which anyone is
    entitled to it; it disappears rather than rendering as "Unknown". That is
    a behaviour change for such rows and not merely a narrowing, which is why
    it is stated here once rather than discovered per route.
    """
    return scope_equipment_query(
        q.join(models.Equipment, model.equipment_id == models.Equipment.id), user
    )


def scope_user_query(db: Session, q, user: models.User):
    """Restrict a User query to the people `user` is allowed to see.

    The roster half of SEC-H5. GET /users had no gate of any kind -- not a role
    check, not a profile flag -- and returned every account in the force with
    its personal_number, which in this domain is the military ID.

    Deliberately NOT scope_equipment_query with a different table. Equipment is
    IN a group by a column; a person is in one by membership, which is
    many-to-many, so the predicate is an EXISTS over GroupMembership rather than
    a comparison on the row. A user visible through two memberships must still
    appear once, which is why this filters rather than joins.

    Two arms, mirroring the equipment rule:

      - membership in a group inside extent(VIEW) -- positional authority, the
        same subtree that decides what equipment you see
      - yourself, always. A private commands nothing and must still be able to
        resolve their own record; without this arm the roster would be empty for
        exactly the people who most need /users/me to work.
      - anyone belonging to NO group, if the caller may manage personnel.

    That third arm is not decoration -- it was found by probing this function
    rather than by reading it, at a time when users.create_user issued no
    membership at all, so a freshly created account was a member of nothing
    and the first two arms hid it from EVERYONE, including the master who
    just created it. H1-12 closed that gap at the source -- create_user now
    requires a group on the request and places the account in the same
    transaction that creates it, the way H1-6 made equipment creation fail
    closed rather than produce an item belonging to nobody.

    The arm stays regardless: a Group can still be deleted out from under a
    member (GroupMembership cascades on the group's ondelete), so an unplaced
    account remains reachable, just rarer. Scoped to MANAGE_PERSONNEL holders
    because placing people is exactly that verb's job, and narrow because
    unplaced is a transient state, not a hiding place -- once a user has any
    membership they scope normally.

    This scopes the ROWS and not the columns, and that distinction used to
    matter more: UserResponse embedded the whole Profile, so a visible peer
    disclosed their permission matrix regardless of what this function scoped.
    H1-12 retired Profile outright rather than reshaping the schema twice, so
    the column-level leak this note used to point at no longer exists.
    """
    visible_group_ids = authz.extent(user.id, Capability.VIEW)
    placed_and_visible = models.User.id.in_(
        select(authz.GroupMembership.user_id).where(
            authz.GroupMembership.group_id.in_(visible_group_ids)
        )
    )
    arms = [placed_and_visible, models.User.id == user.id]

    if authz.may_global(db, user.id, Capability.MANAGE_PERSONNEL):
        arms.append(
            ~models.User.id.in_(select(authz.GroupMembership.user_id))
        )

    return q.filter(or_(*arms))


def get_scoped_equipment_or_404(db: Session, user: models.User, equipment_id: int) -> models.Equipment:
    """Resolve one equipment item within the user's scope.

    Returns 404 rather than 403 for out-of-scope items so that IDs cannot be
    enumerated through THIS lookup. Sibling endpoints that still resolve
    equipment by raw ID remain oracles until they route through here.

    This is the first half of the pair every gated route uses, and the halves
    are not interchangeable:

        resolve here          ->  404 if the caller cannot see it
        authz.require(...)    ->  403 if they see it but may not act

    Run in that order, a 403 only ever reaches someone who could already list
    the item, so it confirms nothing they did not have. Calling require() on an
    id straight from the request body skips the resolver and reinstates exactly
    the oracle this function exists to close -- the shape H1-9 has to fix at
    maintenance.py and verifications.py.

    This used to run a second query ORing the holder back in, because the
    listing scope replaced the holder filter rather than adding to it. It now
    unions both, so one query answers the whole question.
    """
    item = scope_equipment_query(
        db.query(models.Equipment).filter(models.Equipment.id == equipment_id), user
    ).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Equipment not found")
    return item


def require_status_authority(db: Session, user: models.User, item: models.Equipment) -> None:
    """Possession, or REPORT_STATUS over the item's group. Nothing else.

    This is the second half of the resolve-then-decide pair and must run after
    get_scoped_equipment_or_404, never instead of it: it answers 403, and a 403
    is only safe for an item the caller could already list.

    Two of the three arms this replaced are gone. is_master was one -- master
    now holds every capability on the root group, issued by the seed, the
    fixture and bootstrap_admin alike, so the role comparison has something to
    be replaced BY rather than merely removed. can_change_maintenance_status
    was the other, and it did not disappear so much as move: it became
    REPORT_STATUS's placement, where authority is positional and the same
    boolean now says "over this battalion" rather than "everywhere".

    The possession arm survives, deliberately, and it is not a fourth
    mechanism sneaking back in. Holding an item is a fact about the RESOURCE,
    not a claim about who the caller is, which is precisely what the role
    string H1-8 deleted was not. scope_equipment_query already ORs the holder
    into VIEW on the same reasoning -- "a second, independent reason, not a
    fallback for the unprivileged" -- and the alternative is worse in both
    directions: either a private cannot report a fault on the rifle in their
    own hands, or they need a grant over their company and can then write
    status onto every item in it.

    It lives here and only here. fix_equipment deliberately does NOT call this
    function, because closing a fault is not something possession should
    confer: it asks RESOLVE_FAULT directly and a soldier holding a broken item
    can report it and cannot declare it fixed. That asymmetry is the whole
    reason the two verbs exist, and it is enforced by which routes call this
    rather than by anything inside it.

    Takes a Session, unlike the pure predicate it replaced. That is the price
    of asking a question about grants instead of about columns already loaded
    on the user row.
    """
    if item.holder_user_id == user.id:
        return
    authz.require(db, user.id, Capability.REPORT_STATUS, item.group_id)


def get_daily_status(last_verified_at: Optional[datetime]) -> str:
    now_utc = clock.utcnow()
    if not last_verified_at:
        return "SEVERE"
    diff = now_utc - last_verified_at
    if diff < timedelta(hours=24):
        return "GOOD"
    elif diff < timedelta(hours=48):
        return "WARNING"
    else:
        return "SEVERE"
