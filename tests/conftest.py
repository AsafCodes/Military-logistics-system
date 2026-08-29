import os
import tempfile

# --- DATA-H10 containment -------------------------------------------------
# backend/main.py calls wait_for_db() and run_migrations() at MODULE SCOPE, so
# merely importing the app connects to a database and runs Alembic against it.
# backend/database.py defaults DATABASE_URL to sqlite:///./sql_app.db, which
# would make every test run migrate a real file in the repo root.
#
# Point that import-time work at a throwaway file before importing the app.
# The tests themselves do NOT use this database -- they use the in-memory
# engine below, wired in via the get_db dependency override.
#
# Delete this block when DATA-H10 moves both calls into a lifespan handler.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "vector_test_import_sink.db"
)
os.environ.setdefault("SECRET_KEY", "test_secret_key")

# --- SEC-H9 -----------------------------------------------------------------
# The session cookie defaults to Secure (see security._cookie_secure_from_env),
# and TestClient speaks http://testserver. A conforming cookie jar refuses to
# return a Secure cookie over http, so leaving the default on would make every
# cookie-authenticated request in the suite fail for a transport reason rather
# than an authorization one.
#
# Downgraded here to mirror the local http stack. The default itself is pinned
# directly in tests/test_cookie_auth.py, and the Secure attribute is asserted
# there by monkeypatching the constant -- so turning it off here costs no
# coverage of the thing that matters.
#
# ASSIGNED, not setdefault: the suite's correctness depends on this value, so it
# must not yield to whatever the developer happens to have exported. With
# setdefault, a shell carrying COOKIE_SECURE=true made three cookie tests fail
# with a bare 401 and no indication why. Same reasoning as DATABASE_URL above;
# SECRET_KEY differs precisely because any value works there.
os.environ["COOKIE_SECURE"] = "false"
# --------------------------------------------------------------------------

import pytest
from functools import lru_cache
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import Base, get_db, _enforce_sqlite_foreign_keys
from backend import authz, clock, models
from backend.enums import Capability
import backend.security as security
from datetime import timedelta

from sqlalchemy.pool import StaticPool

# Use an in-memory SQLite database for testing
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)

# The suite must run under the same integrity rules as the application, not
# looser ones. Imported rather than re-implemented: a second copy is how the
# suite would drift back into silently tolerating orphaned rows the running
# system rejects. backend.migrations builds its own engine without this,
# because Alembic's batch mode cannot run under enforcement.
event.listen(engine, "connect", _enforce_sqlite_foreign_keys)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="function")
def db_session():
    """Create a fresh database for each test."""
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="function")
def client(db_session):
    """Create a TestClient that uses the test database."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    
    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    del app.dependency_overrides[get_db]

FIXTURE_GROUP_TREE = {
    "188": None,
    "188/53": "188",
    "188/53/A": "188/53",
    "188/53/B": "188/53",
}


@pytest.fixture(scope="function")
def group_graph(db_session):
    """The org chart the matrix fixture sits in, keyed by name.

    Separate from mock_matrix_db because groups do not depend on users, and
    because a test that wants to place an item OUTSIDE this tree needs the
    handles without inheriting the eleven accounts.

    Mirrors seed_data.GROUP_TREE deliberately, but is declared rather than
    imported: the seed is production data and this is fixture data, and a test
    that asserts the seed's shape by reading the seed asserts nothing. Names are
    path-shaped only because Group.name is globally unique; nothing splits them.

    Edges go through authz.add_edge rather than raw GroupEdge rows -- it is the
    public API, it refuses an edge that would close a cycle, and it leaves the
    closure agreeing with the edges when it returns.
    """
    groups = {name: authz.Unit(name=name) for name in FIXTURE_GROUP_TREE}
    db_session.add_all(list(groups.values()))
    db_session.flush()  # add_edge needs the ids; this session does not autoflush

    for name, parent in FIXTURE_GROUP_TREE.items():
        if parent is not None:
            authz.add_edge(db_session, groups[parent].id, groups[name].id)

    db_session.commit()
    return groups


@lru_cache(maxsize=1)
def _fixture_password_hash():
    """The one bcrypt hash every fixture account shares.

    Cached because bcrypt is deliberately slow -- ~240ms per call -- and this
    used to run once per test from inside mock_matrix_db. That single line was
    two thirds of the suite's total runtime: 54s dropped to ~18s when it stopped
    being recomputed, with nothing observable changed, since all eleven accounts
    already shared the one value and no test asserts on hash uniqueness.

    Cached rather than a module-level constant so importing conftest does not
    pay for it -- collection-only runs and `--collect-only` never hash at all.
    """
    return security.get_password_hash("secret")


def revoke(db_session, user, *capabilities):
    """Delete a user's grants, all of them or only the named verbs.

    Lives here because three modules now strip authority to assert what is
    left -- H1-8's, H1-9's and H1-10's -- and the last of those needs the
    all-of-them form to take a master's SIGHT away, which only became
    possible when the is_master bypass was deleted.
    """
    query = db_session.query(authz.Grant).filter(authz.Grant.user_id == user.id)
    if capabilities:
        query = query.filter(
            authz.Grant.capability.in_([c.value for c in capabilities])
        )
    query.delete(synchronize_session=False)
    db_session.commit()


def create_group(db_session, name):
    """A group outside FIXTURE_GROUP_TREE, with the closure left consistent.

    rebuild_closure is not optional and this helper exists so no test has to
    remember that. A Group added on its own has no closure row at all -- not
    even the depth-0 self-row desc(G) is carried by -- so it is unreachable by
    ANY grant, including a direct one over it. A test that skipped the rebuild
    would still see the isolation it was asserting, for the wrong reason.
    Pinned by test_scope_equipment_query.py's closure test.
    """
    group = authz.Unit(name=name)
    db_session.add(group)
    authz.rebuild_closure(db_session)
    db_session.commit()
    return group


@pytest.fixture(scope="function")
def mock_matrix_db(db_session, group_graph):
    """
    Seed the database with the full hierarchy:
    - Master
    - Brigade 1: Brigade Tech Cmdr, Brigade Tech Soldier
    - Bat 1: Bat Tech Cmdr, Bat Tech Soldier
    - Co A: Co Cmdr, Tech Soldier, Soldier (with items)
    - Co B: Co Cmdr, Tech Soldier, Soldier (with items)
    """
    pw = _fixture_password_hash()

    # --- USERS ---
    # role, profile_id, battalion and company are gone (H1-12) along with
    # Profile/UserRole. Where each account sits is stated once, below, as a
    # GroupMembership row.
    users = {}

    # Master
    users["master"] = models.User(
        personal_number="u_master", full_name="Master Admin", password_hash=pw
    )

    # Brigade 1
    users["brigade_cmdr"] = models.User(
        personal_number="u_brig_cmdr", full_name="Cmdr Brigade Tech", password_hash=pw
    )
    users["brigade_tech"] = models.User(
        personal_number="u_brig_tech", full_name="Tech Brigade", password_hash=pw
    )

    # Battalion 1
    users["bat_cmdr"] = models.User(
        personal_number="u_bat_cmdr", full_name="Cmdr Tech Bat 1", password_hash=pw
    )
    users["bat_tech"] = models.User(
        personal_number="u_bat_tech", full_name="Tech Bat 1", password_hash=pw
    )

    # Company A
    users["company_cmdr_a"] = models.User(
        personal_number="u_cmdr_a", full_name="Cmdr Co A", password_hash=pw
    )
    users["company_tech_a"] = models.User(
        personal_number="u_tech_a", full_name="Tech Co A", password_hash=pw
    )
    users["soldier_a"] = models.User(
        personal_number="u_soldier_a", full_name="Soldier Co A", password_hash=pw
    )

    # Company B
    users["company_cmdr_b"] = models.User(
        personal_number="u_cmdr_b", full_name="Cmdr Co B", password_hash=pw
    )
    users["company_tech_b"] = models.User(
        personal_number="u_tech_b", full_name="Tech Co B", password_hash=pw
    )
    users["soldier_b"] = models.User(
        personal_number="u_soldier_b", full_name="Soldier Co B", password_hash=pw
    )

    for u in users.values():
        db_session.add(u)
    db_session.commit()

    # --- GROUP MEMBERSHIPS AND GRANTS ---
    # Where each account SITS -- the only statement of it, since H1-11 dropped
    # the path columns that used to sit on the rows above. Every user is in
    # exactly one group, master included: the path encoding had no node
    # standing for the whole force, so it wrote NULL and called that
    # "unscoped", and the group model has a root to point at instead.
    memberships = {
        "master": "188",
        "brigade_cmdr": "188",
        "brigade_tech": "188",
        "bat_cmdr": "188/53",
        "bat_tech": "188/53",
        "company_cmdr_a": "188/53/A",
        "company_tech_a": "188/53/A",
        "soldier_a": "188/53/A",
        "company_cmdr_b": "188/53/B",
        "company_tech_b": "188/53/B",
        "soldier_b": "188/53/B",
    }

    # What each account may DO, and where. One table per verb, mirroring
    # seed_data.GRANTS in structure but declared rather than imported -- a test
    # that asserts the seed's shape by reading the seed asserts nothing.
    #
    # Placement is positional, so each profile's ladder of booleans reduces to
    # which node its grant sits on. brigade_cmdr lands on the root because the
    # old ladder resolved their path "188" to the prefix "188", which matched
    # every fixture item.
    #
    # VIEW follows the profile ladder, and H1-10 reconciled it with profiles.py
    # rather than with this file's own copy. The Company Tech Soldier row above
    # set can_view_company_realtime FALSE where profiles.py sets it true, and
    # that single disagreement was load-bearing: it left the company techs, the
    # battalion tech and the brigade tech holding maintenance verbs over groups
    # whose items they could not see. Under 404-before-403 the resolver asks
    # VIEW first, so that was authority no request could reach -- see H1-9,
    # which found it and pinned it rather than papering over it.
    #
    # bat_tech and the two company techs are therefore mappings, from
    # can_view_battalion_realtime and can_view_company_realtime.
    #
    # brigade_tech is absent from the literal table BY DESIGN, and that absence
    # is the point of the derivation below. Brigade Tech Soldier has no view
    # flag at all in profiles.py while carrying maintenance authority, so no
    # mapping can supply the row -- H1-10 invented it and labelled it a
    # judgement. It is now DERIVED from the maintenance grants it holds, so the
    # rule produces it rather than a hand-placed literal asserting it.
    #
    # Both soldiers stay absent, deliberately. They see what they hold, and a
    # VIEW grant over their company would take soldier_a from one item to two.
    #
    # TRANSFER follows can_change_assignment_others and lands on the same five
    # nodes as VIEW. That the two tables coincide HERE is a fact about these
    # eleven accounts, not about the model -- seed_data's tables differ, because
    # its u_tech_bat has the view flag without the assignment one. Keeping the
    # verbs in separate tables is what makes a route that reads the wrong one
    # fail somewhere rather than nowhere.
    #
    # CREATE_EQUIPMENT follows can_add_specific_item, and it is the table that
    # does NOT coincide: Company Commander lacks that flag and Company Tech
    # Soldier has it, so the company techs can create where their commanders
    # cannot. That asymmetry is profiles.py's, faithfully carried over.
    #
    # REPORT_STATUS comes from can_change_maintenance_status. That is every
    # profile except plain Soldier, who is covered for their own kit by the
    # possession arm in dependencies.require_status_authority and by nothing
    # else -- a grant over their company would let them write status onto every
    # item in it, which is wider than "report the rifle in your hands".
    #
    # RESOLVE_FAULT is the one table in this file that is a JUDGEMENT rather
    # than a mapping, and it should be read as one. profiles.py has a single
    # maintenance column, so it cannot distinguish reporting a fault from
    # closing one; the split says the technical function closes. In practice
    # that is REPORT_STATUS minus the company commanders, since Company
    # Commander is the only profile with the maintenance boolean that is not a
    # tech. If the two tables ever coincide again, the second verb has stopped
    # meaning anything -- that single exclusion is the whole of the difference.
    #
    # MANAGE_CATALOG and MANAGE_PERSONNEL are the two GLOBAL verbs, and their
    # placement on the root is not decorative: authz.require_global asks for
    # them over every root, so a grant anywhere below the top authorises
    # nothing at all. MANAGE_CATALOG comes from can_add_category and
    # can_remove_category, whose holders coincide; MANAGE_PERSONNEL comes from
    # can_assign_roles, which only the Master profile carries -- so it is the
    # narrowest table here and the one that replaced a role comparison.
    #
    # master's grants are a placement, not a bypass. After H1-8 the equipment
    # routes compare no role at all, so these are the only reason master can
    # transfer or create; is_master still short-circuits VISIBILITY until H1-10.
    grants = {
        Capability.VIEW: {
            "master": "188",
            "brigade_cmdr": "188",
            "bat_cmdr": "188/53",
            "bat_tech": "188/53",
            "company_cmdr_a": "188/53/A",
            "company_tech_a": "188/53/A",
            "company_cmdr_b": "188/53/B",
            "company_tech_b": "188/53/B",
        },
        Capability.TRANSFER: {
            "master": "188",
            "brigade_cmdr": "188",
            "bat_cmdr": "188/53",
            "company_cmdr_a": "188/53/A",
            "company_cmdr_b": "188/53/B",
        },
        Capability.CREATE_EQUIPMENT: {
            "master": "188",
            "brigade_cmdr": "188",
            "bat_cmdr": "188/53",
            "company_tech_a": "188/53/A",
            "company_tech_b": "188/53/B",
        },
        # brigade_cmdr and bat_cmdr are Brigade/Battalion Tech COMMANDER
        # profiles despite reading as line commanders here, so they hold both
        # maintenance verbs. company_cmdr_a and company_cmdr_b are the pair the
        # split is about, and they appear in exactly one of these two tables.
        Capability.REPORT_STATUS: {
            "master": "188",
            "brigade_cmdr": "188",
            "brigade_tech": "188",
            "bat_cmdr": "188/53",
            "bat_tech": "188/53",
            "company_cmdr_a": "188/53/A",
            "company_tech_a": "188/53/A",
            "company_cmdr_b": "188/53/B",
            "company_tech_b": "188/53/B",
        },
        Capability.RESOLVE_FAULT: {
            "master": "188",
            "brigade_cmdr": "188",
            "brigade_tech": "188",
            "bat_cmdr": "188/53",
            "bat_tech": "188/53",
            "company_tech_a": "188/53/A",
            "company_tech_b": "188/53/B",
        },
        Capability.MANAGE_CATALOG: {
            "master": "188",
            "brigade_cmdr": "188",
        },
        Capability.MANAGE_PERSONNEL: {
            "master": "188",
        },
    }

    # THE INVARIANT, stated once and enforced rather than hand-maintained.
    #
    #     a verb over equipment in a group implies VIEW of that group
    #
    # H1-9 and H1-10 each hand-placed a row to satisfy this and each labelled it
    # a judgement asking to be revisited: RESOLVE_FAULT's split from
    # REPORT_STATUS, and brigade_tech's root VIEW. Both were reaching for this
    # rule. Deriving the rows means brigade_tech's grant is no longer invented --
    # it is a consequence of holding maintenance verbs at the root -- and a verb
    # added to a table below can never again be authority nobody can exercise,
    # which is the defect H1-9 found the hard way.
    #
    # The GLOBAL verbs are excluded deliberately. MANAGE_CATALOG and
    # MANAGE_PERSONNEL sit on the root and authorise vocabulary and people, not
    # equipment; folding them in would hand anyone who can create a user sight of
    # every item in the force.
    #
    # The literal VIEW rows above are the independent ones -- the commanders'
    # visibility ladder, which exists whether or not they may act. Union, not
    # replacement: a VIEW grant is allowed to be broader than any single verb.
    #
    # A set of pairs rather than the dict the other verbs use, because VIEW is
    # the one table where an account can legitimately need two rows: its own
    # visibility node and a maintenance node somewhere else. Nobody is in that
    # position today, and the structure should not be what stops them.
    view_rows = set(grants[Capability.VIEW].items()) | authz.implied_view_placements(
        {capability: placements.items() for capability, placements in grants.items()}
    )

    db_session.add_all(
        [
            authz.GroupMembership(
                user_id=users[key].id, group_id=group_graph[name].id
            )
            for key, name in memberships.items()
        ]
        + [
            authz.Grant(
                user_id=users[key].id,
                group_id=group_graph[name].id,
                capability=capability.value,
            )
            for capability, placements in grants.items()
            if capability is not Capability.VIEW
            for key, name in placements.items()
        ]
        + [
            authz.Grant(
                user_id=users[key].id,
                group_id=group_graph[name].id,
                capability=Capability.VIEW.value,
            )
            for key, name in sorted(view_rows)
        ]
    )
    db_session.commit()

    # --- CATALOG ---
    cat = models.CatalogItem(name="Standard Radio")
    db_session.add(cat)
    db_session.commit()
    
    # --- ITEMS ---
    # Give everyone a personal item, plus some unit items
    items = []
    
    def belongs_to(name):
        """Where an item belongs. One name, one column, since H1-11.

        This used to return the path string alongside group_id so that the two
        could not drift. There is nothing left to drift against.
        """
        return {"group_id": group_graph[name].id}

    # Soldier A Item
    items.append(models.Equipment(
        catalog_item_id=cat.id, status="Functional", **belongs_to("188/53/A"),
        holder_user_id=users["soldier_a"].id, owner_user_id=users["soldier_a"].id,
        sensitivity="UNCLASSIFIED", serial_number="SA100", last_verified_at=clock.utcnow()
    ))

    # Soldier B Item (Different Company)
    items.append(models.Equipment(
        catalog_item_id=cat.id, status="Functional", **belongs_to("188/53/B"),
        holder_user_id=users["soldier_b"].id, owner_user_id=users["soldier_b"].id,
        sensitivity="UNCLASSIFIED", serial_number="SB200", last_verified_at=clock.utcnow()
    ))

    # Tech A Item
    items.append(models.Equipment(
        catalog_item_id=cat.id, status="Functional", **belongs_to("188/53/A"),
        holder_user_id=users["company_tech_a"].id, owner_user_id=users["company_tech_a"].id,
        sensitivity="UNCLASSIFIED", serial_number="TA300", last_verified_at=clock.utcnow()
    ))
    
    for item in items:
        db_session.add(item)
    db_session.commit()

    return users

# --- AUTH FIXTURES ---

def create_auth_header(user_personal_number: str):
    access_token_expires = timedelta(minutes=30)
    access_token = security.create_access_token(
        data={"sub": user_personal_number}, expires_delta=access_token_expires
    )
    return {"Authorization": f"Bearer {access_token}"}

@pytest.fixture
def token_master(mock_matrix_db): return create_auth_header("u_master")

@pytest.fixture
def token_brigade_cmdr(mock_matrix_db): return create_auth_header("u_brig_cmdr")

@pytest.fixture
def token_brigade_tech(mock_matrix_db): return create_auth_header("u_brig_tech")

@pytest.fixture
def token_bat_cmdr(mock_matrix_db): return create_auth_header("u_bat_cmdr")

@pytest.fixture
def token_bat_tech(mock_matrix_db): return create_auth_header("u_bat_tech")

@pytest.fixture
def token_company_cmdr(mock_matrix_db): return create_auth_header("u_cmdr_a") # Co A Commander

@pytest.fixture
def token_company_tech(mock_matrix_db): return create_auth_header("u_tech_a") # Co A Tech

@pytest.fixture
def token_soldier(mock_matrix_db): return create_auth_header("u_soldier_a") # Co A Soldier
