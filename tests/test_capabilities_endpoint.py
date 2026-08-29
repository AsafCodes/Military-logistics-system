"""SEC-H10-1: GET /users/me/capabilities.

The frontend has no way to ask "what may I do" -- SEC-H10's own text names
that gap as the reason the /admin route and several equipment/maintenance
controls only ever pretend to be gated. This endpoint is the answer, and its
entire value is that it cannot disagree with authz.may_global/authz.extent --
the functions every real gate already calls. So the test that matters most
here is not "does account X see verb Y" (test_global_authority.py and
test_permissions_matrix.py already prove the underlying algebra); it is
"does the endpoint agree with the algebra for every account and every verb,
without exception."

GLOBAL_CAPABILITIES/SCOPED_CAPABILITIES existing at all is new with this
ticket -- until now, which verbs are flat booleans and which are positional
was implicit in which routes called require_global versus require. A few
tests here pin that partition directly, since a verb landing on the wrong
side is a silent, endpoint-wide correctness bug (a positional verb reported
as "you have it everywhere", or a global verb quietly dropped and never
reported at all).
"""
import pytest

from backend import authz
from backend.enums import Capability
from tests.conftest import create_auth_header


def capabilities(client, who):
    return client.get("/users/me/capabilities", headers=create_auth_header(who))


# --- 1. Agreement: the endpoint cannot disagree with the gate ---------------


def test_agrees_with_the_algebra_for_every_account_and_every_capability(client, mock_matrix_db, db_session):
    """The property that matters. Computed independently of the fixture's
    grant tables -- this recomputes may_global/extent directly against the
    live session for each (user, capability) pair and compares against what
    the route reports, rather than hand-listing expected sets (that is
    test_reports_the_documented_matrix below, and it's a second, independent
    check -- not a substitute for this one).
    """
    for key, user in mock_matrix_db.items():
        response = capabilities(client, user.personal_number)
        assert response.status_code == 200
        body = response.json()

        expected_system = {
            c.value for c in authz.GLOBAL_CAPABILITIES
            if authz.may_global(db_session, user.id, c)
        }
        expected_anywhere = {
            c.value for c in authz.SCOPED_CAPABILITIES
            if db_session.execute(authz.extent(user.id, c).limit(1)).first() is not None
        }

        assert set(body["system"]) == expected_system, key
        assert set(body["anywhere"]) == expected_anywhere, key


# --- 2. The documented matrix, hand-written -----------------------------
#
# A second, independent source of truth. Asserting the fixture by reading the
# fixture (test 1 above) proves internal consistency, not correctness -- if
# GLOBAL_CAPABILITIES or the route itself mis-sorted a verb in exactly the way
# extent()/may_global would too, test 1 would not catch it. These numbers are
# transcribed from the grants table at conftest.py:336-392 and
# implied_view_placements, independently of the route or authz module.

EXPECTED = {
    "master": (
        {"MANAGE_PERSONNEL", "MANAGE_CATALOG"},
        {"VIEW", "TRANSFER", "CREATE_EQUIPMENT", "REPORT_STATUS", "RESOLVE_FAULT"},
    ),
    "brigade_cmdr": (
        {"MANAGE_CATALOG"},
        {"VIEW", "TRANSFER", "CREATE_EQUIPMENT", "REPORT_STATUS", "RESOLVE_FAULT"},
    ),
    # Absent from the literal VIEW table entirely -- holds it only via
    # implied_view_placements, derived from the REPORT_STATUS/RESOLVE_FAULT
    # grants below. If that derivation ever breaks, this is the row that fails.
    "brigade_tech": (set(), {"VIEW", "REPORT_STATUS", "RESOLVE_FAULT"}),
    "bat_cmdr": (
        set(),
        {"VIEW", "TRANSFER", "CREATE_EQUIPMENT", "REPORT_STATUS", "RESOLVE_FAULT"},
    ),
    "bat_tech": (set(), {"VIEW", "REPORT_STATUS", "RESOLVE_FAULT"}),
    "company_cmdr_a": (set(), {"VIEW", "TRANSFER", "REPORT_STATUS"}),
    "company_tech_a": (set(), {"VIEW", "CREATE_EQUIPMENT", "REPORT_STATUS", "RESOLVE_FAULT"}),
    # No grant of any kind. The floor case: a real account, correctly seeded,
    # that the endpoint must answer with two empty lists rather than an error
    # or a permissive default.
    "soldier_a": (set(), set()),
    "company_cmdr_b": (set(), {"VIEW", "TRANSFER", "REPORT_STATUS"}),
    "company_tech_b": (set(), {"VIEW", "CREATE_EQUIPMENT", "REPORT_STATUS", "RESOLVE_FAULT"}),
    "soldier_b": (set(), set()),
}


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_reports_the_documented_matrix(client, mock_matrix_db, key):
    expected_system, expected_anywhere = EXPECTED[key]
    body = capabilities(client, mock_matrix_db[key].personal_number).json()
    assert set(body["system"]) == expected_system
    assert set(body["anywhere"]) == expected_anywhere


# --- 3. The partition itself ------------------------------------------------


def test_global_and_scoped_partition_every_capability_exactly_once():
    global_set = set(authz.GLOBAL_CAPABILITIES)
    scoped_set = set(authz.SCOPED_CAPABILITIES)

    assert global_set.isdisjoint(scoped_set)
    assert global_set | scoped_set == set(Capability)


# --- 4. Empty is empty, not a default and not an error ---------------------


def test_an_account_with_no_grants_gets_two_empty_lists_not_an_error(client, mock_matrix_db):
    body = capabilities(client, mock_matrix_db["soldier_a"].personal_number).json()
    assert body == {"system": [], "anywhere": []}


# --- 5. Route shadowing ------------------------------------------------


def test_users_me_capabilities_does_not_fall_through_to_a_users_id_route(client, mock_matrix_db):
    """FastAPI matches routes in registration order. Nothing today declares a
    GET /users/{user_id}... pattern this could shadow behind, but the ordering
    is exactly the kind of thing a later add could get backwards silently --
    pin it so that regression is loud.
    """
    response = capabilities(client, mock_matrix_db["soldier_a"].personal_number)
    assert response.status_code == 200
    assert set(response.json().keys()) == {"system", "anywhere"}


# --- 6. Authentication -------------------------------------------------


def test_unauthenticated_request_is_refused(client):
    assert client.get("/users/me/capabilities").status_code == 401


def test_a_user_deactivated_mid_session_is_refused(client, mock_matrix_db, db_session):
    mock_matrix_db["soldier_a"].is_active_duty = False
    db_session.commit()

    response = capabilities(client, mock_matrix_db["soldier_a"].personal_number)
    assert response.status_code == 400  # get_current_active_user's shape; see SEC-L5


# --- 7. Fails closed on the sharp edge authz.py:485-489 names --------------


def test_a_grant_on_a_group_with_no_closure_rows_is_not_reported(client, mock_matrix_db, db_session):
    """create_group (conftest.py) always rebuilds the closure; this test
    deliberately skips that step to reproduce the documented sharp edge -- a
    Group persisted without ever calling rebuild_closure carries no closure
    rows at all, not even its own depth-0 self-row. authz.py:485-489 warns
    that such a group is invisible to desc()/extent() and therefore to every
    grant placed on it. The capabilities endpoint must inherit that failure
    mode as "verb absent", not surface it as an unhandled error or, worse,
    silently report the verb present.
    """
    orphan = authz.Unit(name="orphan-no-closure")
    db_session.add(orphan)
    db_session.commit()  # no authz.rebuild_closure(db_session) -- the point of the test

    db_session.add(authz.Grant(
        user_id=mock_matrix_db["soldier_a"].id,
        group_id=orphan.id,
        capability=Capability.TRANSFER.value,
    ))
    db_session.commit()

    body = capabilities(client, mock_matrix_db["soldier_a"].personal_number).json()
    assert "TRANSFER" not in body["anywhere"]


# --- 8. No cross-user disclosure --------------------------------------------


def test_two_accounts_get_their_own_answers_not_each_others(client, mock_matrix_db):
    master_body = capabilities(client, mock_matrix_db["master"].personal_number).json()
    soldier_body = capabilities(client, mock_matrix_db["soldier_a"].personal_number).json()

    assert master_body != soldier_body
    assert soldier_body == {"system": [], "anywhere": []}
