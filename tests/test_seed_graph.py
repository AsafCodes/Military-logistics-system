"""Behavioural coverage for H1-4 (TODO-SEC-H1.md) -- the seeded group graph.

The seed is a script, and until this module nothing in the suite had ever run
it. So these tests run it the way an operator does: `python -m backend.seed_data
--reset` in a subprocess, against a throwaway database, with real migrations.
Importing seed_matrix() and calling it in-process would test a different thing
-- backend/seed_data.py binds its session at module scope to whatever
DATABASE_URL said at import time, which the suite has already pointed at its own
sink (conftest.py:16).

What H1-4 claimed was an equivalence -- the group graph reproduces, over real
seeded rows, exactly the visibility the path ladder produced. H1-5 deleted that
ladder, so what remains is the half that outlives it: the seeded grants resolve,
per account, to exactly the intended set of serial numbers. Checked as a set,
never as a count -- u_bn_cmdr and u_tech_bat both see 22 items, so swapping
their grants passes any count check.

One seed, shared by the module: it costs ~3.5s, most of it bcrypt hashing seven
passwords. Everything below reads that one database.
"""
import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend import authz, models
from backend.dependencies import scope_equipment_query
from backend.enums import Capability, GroupKind

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Restated rather than imported from seed_data.GROUP_TREE. A test that reads the
# declaration it is checking cannot catch a wrong declaration -- it would agree
# with any org chart, including an empty one. This is the shape the seed is
# supposed to produce, written down independently.
EXPECTED_GROUPS = {"188", "188/53", "188/53/A", "188/53/B"}
EXPECTED_EDGES = {
    ("188", "188/53"),
    ("188/53", "188/53/A"),
    ("188/53", "188/53/B"),
}

# Every reachable pair with its shortest-path depth, including the depth-0 self
# rows that make desc(G) include G. Asserted as triples rather than as a row
# count: a wrong depth, or one pair swapped for another, keeps the count at nine.
EXPECTED_CLOSURE = {
    ("188", "188", 0),
    ("188", "188/53", 1),
    ("188", "188/53/A", 2),
    ("188", "188/53/B", 2),
    ("188/53", "188/53", 0),
    ("188/53", "188/53/A", 1),
    ("188/53", "188/53/B", 1),
    ("188/53/A", "188/53/A", 0),
    ("188/53/B", "188/53/B", 0),
}

EXPECTED_MEMBERSHIPS = {
    ("u_master", "188"),
    ("u_brig_cmdr", "188"),
    ("u_bn_cmdr", "188/53"),
    ("u_tech_bat", "188/53"),
    ("u_co_cmdr_a", "188/53/A"),
    ("u_co_cmdr_b", "188/53/B"),
    ("u_soldier", "188/53/A"),
}

# Six, not seven. u_soldier's absence is a claim this suite makes, not an
# oversight -- see test_the_soldier_holds_an_item_but_commands_no_group.
EXPECTED_GRANTS = {
    ("u_master", "188", "VIEW"),
    ("u_brig_cmdr", "188", "VIEW"),
    ("u_bn_cmdr", "188/53", "VIEW"),
    ("u_tech_bat", "188/53", "VIEW"),
    ("u_co_cmdr_a", "188/53/A", "VIEW"),
    ("u_co_cmdr_b", "188/53/B", "VIEW"),
    # TRANSFER: can_change_assignment_others. VIEW's placement minus
    # u_tech_bat, whose Battalion Tech SOLDIER profile has the view flag and
    # not the assignment one. That one exclusion is what SEC-H3's name
    # allowlist used to override.
    ("u_master", "188", "TRANSFER"),
    ("u_brig_cmdr", "188", "TRANSFER"),
    ("u_bn_cmdr", "188/53", "TRANSFER"),
    ("u_co_cmdr_a", "188/53/A", "TRANSFER"),
    ("u_co_cmdr_b", "188/53/B", "TRANSFER"),
    # CREATE_EQUIPMENT: can_add_specific_item. Neither company commander holds
    # that flag, and the seed has no company tech, so this table is the three
    # accounts at battalion level and above.
    ("u_master", "188", "CREATE_EQUIPMENT"),
    ("u_brig_cmdr", "188", "CREATE_EQUIPMENT"),
    ("u_bn_cmdr", "188/53", "CREATE_EQUIPMENT"),
    # REPORT_STATUS: can_change_maintenance_status, which every seeded profile
    # carries except plain Soldier. u_soldier is therefore absent, and reports
    # on their own kit through the possession arm rather than through a row
    # here -- see dependencies.require_status_authority.
    ("u_master", "188", "REPORT_STATUS"),
    ("u_brig_cmdr", "188", "REPORT_STATUS"),
    ("u_bn_cmdr", "188/53", "REPORT_STATUS"),
    ("u_tech_bat", "188/53", "REPORT_STATUS"),
    ("u_co_cmdr_a", "188/53/A", "REPORT_STATUS"),
    ("u_co_cmdr_b", "188/53/B", "REPORT_STATUS"),
    # RESOLVE_FAULT: REPORT_STATUS minus the two company commanders, the only
    # profile holding the maintenance boolean that is not a tech. Those two
    # absences are the entire difference between the verbs, so a table that
    # ever matched REPORT_STATUS row for row would mean the split had quietly
    # stopped meaning anything -- which is exactly what this set catches.
    ("u_master", "188", "RESOLVE_FAULT"),
    ("u_brig_cmdr", "188", "RESOLVE_FAULT"),
    ("u_bn_cmdr", "188/53", "RESOLVE_FAULT"),
    ("u_tech_bat", "188/53", "RESOLVE_FAULT"),
    # The two GLOBAL verbs. Their placement on the root is load-bearing in a
    # way the others are not: require_global asks for them over EVERY root, so
    # a row here naming any node below the top would authorise nothing at all
    # while looking perfectly ordinary in this set.
    #
    # MANAGE_CATALOG follows can_add_category and can_remove_category, whose
    # holders coincide. MANAGE_PERSONNEL follows can_assign_roles, which only
    # Master carries -- one row, and it is the one that replaced
    # verify_admin_access's role comparison.
    ("u_master", "188", "MANAGE_CATALOG"),
    ("u_brig_cmdr", "188", "MANAGE_CATALOG"),
    ("u_master", "188", "MANAGE_PERSONNEL"),
}

# The seeded inventory, by the group holding it.
BRIGADE_ITEMS = {"BRIG-001"}
BATTALION_ITEMS = {"BAT-001"}
CO_A_ITEMS = {"9876543"} | {f"CO-A-{i}" for i in range(10)}
CO_B_ITEMS = {f"CO-B-{i}" for i in range(10)}
ALL_ITEMS = BRIGADE_ITEMS | BATTALION_ITEMS | CO_A_ITEMS | CO_B_ITEMS

# What each account is supposed to be able to see, stated outright.
#
# Written as a literal on purpose, rather than as "whatever the endpoint
# returns". This table predates H1-5 and was written against the path ladder
# scope_equipment_query used to run; asserting helper == extent() would have
# held for the wrong reason the moment H1-5 rewrote that helper to CALL
# extent(), since both sides become the same query and the comparison passes
# unconditionally. Anchoring both to this table instead is what carried the
# assertion across the cutover intact.
EXPECTED_VISIBLE = {
    "u_master": ALL_ITEMS,
    "u_brig_cmdr": ALL_ITEMS,
    "u_bn_cmdr": BATTALION_ITEMS | CO_A_ITEMS | CO_B_ITEMS,
    "u_tech_bat": BATTALION_ITEMS | CO_A_ITEMS | CO_B_ITEMS,
    "u_co_cmdr_a": CO_A_ITEMS,
    "u_co_cmdr_b": CO_B_ITEMS,
}

# The subtree each grant opens up: desc() of the granted group, by name.
EXPECTED_EXTENTS = {
    "u_master": EXPECTED_GROUPS,
    "u_brig_cmdr": EXPECTED_GROUPS,
    "u_bn_cmdr": {"188/53", "188/53/A", "188/53/B"},
    "u_tech_bat": {"188/53", "188/53/A", "188/53/B"},
    "u_co_cmdr_a": {"188/53/A"},
    "u_co_cmdr_b": {"188/53/B"},
    "u_soldier": set(),
}

GRANT_HOLDERS = sorted({personal_number for personal_number, _, _ in EXPECTED_GRANTS})


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    """Run the seed as shipped, and hand back a session on what it produced.

    The seed prints emoji and Hebrew, which makes encoding load-bearing at both
    ends of the pipe (DATA-M20).

    PYTHONIOENCODING tells the child how to write. Without it, encoding those
    characters to a captured pipe under a cp1255 locale raises
    UnicodeEncodeError and takes the seed down with it.

    encoding= tells subprocess how to read, and text=True is not a substitute:
    it decodes with the parent's locale, which fails on the same bytes. The
    failure is quiet in the worst way -- it happens on a reader thread, so
    result.stdout comes back empty rather than raising, and the traceback
    surfaces only as a pytest warning. stderr survives that today, but only
    because the seed's tracebacks happen to be ASCII; the Hebrew profile fields
    are one IntegrityError away from being in one.
    """
    db_path = tmp_path_factory.mktemp("seed") / "seeded.db"
    url = f"sqlite:///{db_path.as_posix()}"

    result = subprocess.run(
        [sys.executable, "-m", "backend.seed_data", "--reset"],
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "SEED_ENABLED": "1",
            "DATABASE_URL": url,
            "PYTHONIOENCODING": "utf-8",
        },
        capture_output=True,
        encoding="utf-8",
        # Explicitly not check=True: CalledProcessError's message does not carry
        # the child's stderr, and the whole point of capturing it is to put the
        # seed's own traceback in the failure below.
        check=False,
    )
    assert result.returncode == 0, (
        f"the seed failed (exit {result.returncode}):\n{result.stderr}"
    )

    engine = create_engine(url)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        # Both, and in this order: Windows will not let pytest remove the tmp
        # directory while a connection still holds the file open.
        session.close()
        engine.dispose()


@pytest.fixture(scope="module")
def group_names(seeded):
    """id -> name, so assertions can talk about "188/53" instead of about 2."""
    return {group.id: group.name for group in seeded.query(authz.Group).all()}


def names_of(group_names, ids):
    """Group names for `ids`, refusing duplicates.

    desc(G) is a set. A duplicate here would mean the closure holds two rows for
    one pair -- the silent result-set multiplication GroupClosure is shaped to
    prevent -- and a plain set() in the caller would hide it.
    """
    ids = list(ids)
    assert len(ids) == len(set(ids)), f"duplicate group ids: {ids}"
    return {group_names[group_id] for group_id in ids}


def user_named(seeded, personal_number):
    return seeded.query(models.User).filter_by(personal_number=personal_number).one()


def serials_visible_via_groups(seeded, user):
    """The items `user` can see under the new model: group in extent(user, VIEW)."""
    return {
        item.serial_number
        for item in seeded.query(models.Equipment).filter(
            models.Equipment.group_id.in_(authz.extent(user.id, Capability.VIEW))
        )
    }


def serials_visible_via_endpoint(seeded, user):
    """The items `user` can see through the helper every read path shares.

    Since H1-5 this composes extent() itself, so it agrees with
    serials_visible_via_groups by construction for a grant holder. Both are
    still checked, against the literal table rather than against each other --
    what this arm adds is the holder union and the is_master short-circuit,
    which the raw extent above does not carry.
    """
    return {
        item.serial_number
        for item in scope_equipment_query(seeded.query(models.Equipment), user)
    }


# --- the graph itself ------------------------------------------------------

def test_the_seed_builds_the_declared_org_chart(seeded, group_names):
    """Four groups, three edges, and every one of them a Unit.

    The kinds matter as much as the shape: a TaskForce here would mean the seed
    invented a formation to demonstrate the DAG, which belongs in fixtures.
    """
    assert set(group_names.values()) == EXPECTED_GROUPS
    assert {group.kind for group in seeded.query(authz.Group)} == {GroupKind.UNIT.value}

    edges = {
        (group_names[edge.parent_id], group_names[edge.child_id])
        for edge in seeded.query(authz.GroupEdge)
    }
    assert edges == EXPECTED_EDGES


def test_the_closure_is_exactly_the_containment_relation(seeded, group_names):
    """Every reachable pair, at its shortest depth, and nothing else."""
    closure = {
        (group_names[row.ancestor_id], group_names[row.descendant_id], row.depth)
        for row in seeded.query(authz.GroupClosure)
    }
    assert closure == EXPECTED_CLOSURE
    assert seeded.query(authz.GroupClosure).count() == len(EXPECTED_CLOSURE), (
        "the closure holds a duplicate row for some pair"
    )


def test_the_graph_is_connected_from_the_root(seeded, group_names):
    """The Done-when clause: one root that reaches every other group.

    A seed that created four groups and never linked them would satisfy every
    other structural check here except the closure one.
    """
    root = seeded.query(authz.Group).filter_by(name="188").one()
    reached = names_of(group_names, seeded.scalars(authz.descendant_ids([root.id])))
    assert reached == EXPECTED_GROUPS


def test_the_seeded_database_holds_no_broken_references(seeded):
    """The seed is the first thing to write these tables in bulk.

    Cheap, and it covers the whole file rather than only the rows named above --
    including the legacy tables the new rows point into.
    """
    assert seeded.execute(text("PRAGMA foreign_key_check")).fetchall() == []


# --- who sits where, and who commands what ---------------------------------

def test_every_account_sits_in_exactly_one_group(seeded, group_names):
    """Including u_master, who used to sit nowhere at all.

    The old encoding wrote NULL for master and called it "unscoped", only
    because a path string had no node standing for the whole force. The group model has one, and H1-6 derives a new item's
    group from its creator's membership -- a master with no membership would
    create equipment belonging to nobody.
    """
    memberships = {
        (seeded.get(models.User, m.user_id).personal_number, group_names[m.group_id])
        for m in seeded.query(authz.GroupMembership)
    }
    assert memberships == EXPECTED_MEMBERSHIPS
    assert seeded.query(authz.GroupMembership).count() == len(EXPECTED_MEMBERSHIPS)


def test_the_seeded_grants_are_exactly_the_ones_intended(seeded, group_names):
    """One grant more is a widening, and an exact set is the only way to catch it.

    Every other assertion in this file reads VIEW, so a stray TRANSFER or
    CREATE_EQUIPMENT row would change who can write and surface nowhere at all.
    The table is written per verb rather than per account for the same reason:
    the three do not coincide, and reading down a column is how you see that.
    """
    grants = {
        (
            seeded.get(models.User, grant.user_id).personal_number,
            group_names[grant.group_id],
            grant.capability,
        )
        for grant in seeded.query(authz.Grant)
    }
    assert grants == EXPECTED_GRANTS
    assert seeded.query(authz.Grant).count() == len(EXPECTED_GRANTS)


@pytest.mark.parametrize("personal_number", sorted(EXPECTED_EXTENTS))
def test_each_account_commands_the_subtree_under_its_grant(
    seeded, group_names, personal_number
):
    """extent(P, VIEW) resolved to group names, per account.

    Kept separate from the equipment checks below because it fails differently:
    this names the group that went wrong, which a diff of serial numbers does
    not.
    """
    user = user_named(seeded, personal_number)
    extent = names_of(
        group_names, seeded.scalars(authz.extent(user.id, Capability.VIEW))
    )
    assert extent == EXPECTED_EXTENTS[personal_number]


# The subtree each MAINTENANCE grant opens up, per verb. Placement is asserted
# by EXPECTED_GRANTS above; this is what the placement REACHES once the closure
# is applied, which is a different claim and the one H1-9 turns on.
EXPECTED_MAINTENANCE_EXTENTS = {
    "REPORT_STATUS": {
        "u_master": EXPECTED_GROUPS,
        "u_brig_cmdr": EXPECTED_GROUPS,
        "u_bn_cmdr": {"188/53", "188/53/A", "188/53/B"},
        "u_tech_bat": {"188/53", "188/53/A", "188/53/B"},
        "u_co_cmdr_a": {"188/53/A"},
        "u_co_cmdr_b": {"188/53/B"},
        "u_soldier": set(),
    },
    "RESOLVE_FAULT": {
        "u_master": EXPECTED_GROUPS,
        "u_brig_cmdr": EXPECTED_GROUPS,
        "u_bn_cmdr": {"188/53", "188/53/A", "188/53/B"},
        "u_tech_bat": {"188/53", "188/53/A", "188/53/B"},
        # The two rows that are the whole point. Company Commander carries
        # can_change_maintenance_status and is the only profile holding it
        # that is not a tech, so it reports and does not close.
        "u_co_cmdr_a": set(),
        "u_co_cmdr_b": set(),
        "u_soldier": set(),
    },
}


@pytest.mark.parametrize("capability", [Capability.REPORT_STATUS, Capability.RESOLVE_FAULT])
@pytest.mark.parametrize("personal_number", sorted(EXPECTED_MAINTENANCE_EXTENTS["REPORT_STATUS"]))
def test_each_account_reaches_the_units_its_maintenance_grant_names(
    seeded, group_names, personal_number, capability
):
    """extent(P, C) for both maintenance verbs, resolved to group names.

    The two tables are identical except for the company commanders, and that
    single divergence is the entire difference between the verbs. Collapse it --
    grant RESOLVE_FAULT wherever REPORT_STATUS sits, which is the obvious thing
    to do when someone later notices the tables 'nearly match' -- and the second
    verb stops meaning anything while every route-level test still passes,
    because it is granted rather than checked differently.

    Asserted on extents rather than on Grant rows because a grant over a group
    with no closure rows authorises nothing while looking perfectly correct in
    the table.
    """
    user = user_named(seeded, personal_number)
    extent = names_of(group_names, seeded.scalars(authz.extent(user.id, capability)))
    assert extent == EXPECTED_MAINTENANCE_EXTENTS[capability.value][personal_number]


def test_every_equipment_verb_is_matched_by_sight_of_the_same_group(seeded, group_names):
    """The invariant H1-10.5 replaced two hand-placed judgements with.

        a verb over equipment in a group implies VIEW of that group

    H1-9 and H1-10 each added a row to satisfy this and each labelled it a
    judgement asking to be revisited -- RESOLVE_FAULT's split, and
    brigade_tech's root VIEW. Both were reaching for the rule above, which is
    now derived in seed_data and conftest rather than hand-maintained.

    Asserted here because a derivation that is never checked is just code that
    happens to run. This is the property; the derivation is one way to hold it.

    Stated on GRANT ROWS rather than on extents, and the distinction matters:
    a VIEW grant higher up would cover the node by descent and satisfy any
    extent-based check, while leaving the specific pairing this rule is about
    unstated. The rule is about placement.

    The GLOBAL verbs are excluded deliberately -- MANAGE_CATALOG and
    MANAGE_PERSONNEL authorise vocabulary and people, not equipment, and
    folding them in would give anyone who can create a user sight of the whole
    force.
    """
    equipment_verbs = {
        "TRANSFER", "CREATE_EQUIPMENT", "REPORT_STATUS", "RESOLVE_FAULT",
    }
    rows = {
        (personal_number, node)
        for personal_number, node, capability in EXPECTED_GRANTS
        if capability in equipment_verbs
    }
    view = {
        (personal_number, node)
        for personal_number, node, capability in EXPECTED_GRANTS
        if capability == "VIEW"
    }

    assert rows - view == set(), (
        "these hold a verb over equipment in a group they cannot see"
    )


# --- equipment -------------------------------------------------------------

def test_every_seeded_item_is_placed_in_a_group_that_exists(seeded, group_names):
    """The Done-when clause, and what survives of the drift guard.

    This used to compare two representations of one fact and insist they
    agreed row for row. H1-11 dropped the second one, so there is nothing
    left to disagree with -- which was the point of dropping it. What still
    means something is that every item is placed, and placed somewhere real:
    a group_id is now the only thing standing between an item and being
    visible to nobody at all.
    """
    items = seeded.query(models.Equipment).all()
    assert {item.serial_number for item in items} == ALL_ITEMS

    unplaced = [item.serial_number for item in items if item.group_id is None]
    assert unplaced == []

    # Placed at a group the graph actually contains. A dangling group_id is
    # only refused by the foreign key on Postgres (see authz.py on the
    # SQLite pragma), so the seed's own rows are checked here directly.
    dangling = [
        (item.serial_number, item.group_id)
        for item in items
        if item.group_id not in group_names
    ]
    assert dangling == []


@pytest.mark.parametrize("personal_number", GRANT_HOLDERS)
def test_each_grant_holder_sees_exactly_the_subtree_under_their_grant(
    seeded, personal_number
):
    """What the seeded grants actually resolve to, over real rows, item by item.

    Both the endpoint's helper and raw extent() are checked against
    EXPECTED_VISIBLE rather than against each other -- see the note there. That
    is what let this assertion survive H1-5 rewriting the helper to call
    extent(); comparing the two directly would now be a self-comparison.

    Not a count, either: u_bn_cmdr and u_tech_bat both see 22 items, so swapping
    their grants would pass any count-based check while changing nothing
    observable.
    """
    user = user_named(seeded, personal_number)
    expected = EXPECTED_VISIBLE[personal_number]

    assert serials_visible_via_endpoint(seeded, user) == expected
    assert serials_visible_via_groups(seeded, user) == expected


def test_the_soldier_holds_an_item_but_commands_no_group(seeded):
    """The one intended divergence, asserted rather than skipped.

    A soldier sees what they carry, which is not a claim about groups. Granting
    them VIEW over their company would take them from one item to eleven, so the
    seed issues no grant at all and H1-5 carries their visibility on the holder
    arm instead. Pinning the exact size of the gap is what stops someone closing
    it by widening the grant rather than by handling holders.
    """
    soldier = user_named(seeded, "u_soldier")

    assert serials_visible_via_endpoint(seeded, soldier) == {"9876543"}
    assert serials_visible_via_groups(seeded, soldier) == set()
    assert seeded.query(authz.Grant).filter_by(user_id=soldier.id).count() == 0

    # A member of their company all the same: membership says where someone
    # sits, a grant says what they command, and only the second is withheld.
    held = seeded.query(models.Equipment).filter_by(serial_number="9876543").one()
    assert held.holder_user_id == soldier.id
    assert seeded.query(authz.GroupMembership).filter_by(user_id=soldier.id).count() == 1


def test_the_company_commanders_cannot_see_each_other(seeded):
    """Sibling isolation -- the property the whole model exists to provide.

    Implied by the extent tests, but stated outright because it is the one a
    reader will come to this file looking for.
    """
    a_sees = serials_visible_via_groups(seeded, user_named(seeded, "u_co_cmdr_a"))
    b_sees = serials_visible_via_groups(seeded, user_named(seeded, "u_co_cmdr_b"))

    assert a_sees & b_sees == set()
    assert a_sees == CO_A_ITEMS
    assert b_sees == CO_B_ITEMS
