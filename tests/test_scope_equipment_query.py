"""Properties of the scoping predicate itself, below the HTTP layer.

The endpoint suites assert what particular accounts see. This module asserts
the things that must hold for ANY account, and it is where the defect class
SEC-H1 replaced is kept dead: the old predicate matched a materialised path
with an unescaped LIKE, so a name could be hostile. Names are now inert data
and containment is a join, which is a claim worth testing with names chosen to
break the old code rather than trusting that the line is gone.

It also covers get_scoped_equipment_or_404, which had no test of any kind
despite being the 404-not-403 guard standing in front of /verifications.
"""
import pytest
from fastapi import HTTPException

from backend import authz, models
from backend.dependencies import get_scoped_equipment_or_404, scope_equipment_query
from backend.enums import Capability
from tests.conftest import create_auth_header, create_group


def visible(db_session, user):
    """Serial numbers `user` can see, straight through the predicate."""
    return {
        item.serial_number
        for item in scope_equipment_query(db_session.query(models.Equipment), user)
    }


def place(db_session, serial, group=None, holder=None):
    """An item in a group, held by someone, or neither."""
    item = models.Equipment(
        catalog_item_id=db_session.query(models.CatalogItem).first().id,
        status="Functional",
        serial_number=serial,
        group_id=group.id if group is not None else None,
        holder_user_id=holder.id if holder is not None else None,
    )
    db_session.add(item)
    db_session.commit()
    return item


def grant_view(db_session, user, group):
    db_session.add(authz.Grant(
        user_id=user.id, group_id=group.id, capability=Capability.VIEW.value
    ))
    db_session.commit()


# --- the defect class SEC-H1 replaced, kept dead ---------------------------

@pytest.mark.parametrize("hostile", ["188/5", "%", "188%", "_88/53", "188/53/_"])
def test_a_hostile_group_name_grants_nothing_beyond_its_own_subtree(
    db_session, mock_matrix_db, group_graph, hostile
):
    """Every one of these names would have widened the old prefix match.

    "188/5" is a string-prefix of sibling "188/53" -- the SEC-H2 collision this
    module's ancestor xfailed. "%" and "_" are LIKE metacharacters that the old
    startswith() compiled through unescaped, so a user parked on "%" read the
    entire force.

    None of them can do anything now, and the reason is structural rather than
    defensive: no escaping was added, the name is simply never compared to
    anything. Containment is an edge, and these groups have none.
    """
    intruder = mock_matrix_db["soldier_b"]
    assert visible(db_session, intruder) == {"SB200"}, "precondition: holder only"

    grant_view(db_session, intruder, create_group(db_session, hostile))

    assert visible(db_session, intruder) == {"SB200"}, (
        f"a VIEW grant on a group named {hostile!r} reached outside itself"
    )


def test_a_grant_on_a_hostile_name_still_works_normally_for_its_own_items(
    db_session, mock_matrix_db, group_graph
):
    """The mirror of the test above, so that one cannot pass by the grant
    silently doing nothing at all. A group named "%" is an ordinary group."""
    user = mock_matrix_db["soldier_b"]
    wildcard = create_group(db_session, "%")
    grant_view(db_session, user, wildcard)
    place(db_session, "WILDCARD_ITEM", group=wildcard)

    assert visible(db_session, user) == {"SB200", "WILDCARD_ITEM"}


# --- the two arms, separately ----------------------------------------------

def test_membership_conveys_no_visibility(db_session, mock_matrix_db, group_graph):
    """Soldier A is IN Co A and commands none of it.

    Being a member and holding authority are different facts, and the whole
    model rests on not conflating them. A soldier who could see their company
    because they stand in it would make GroupMembership a grant.
    """
    soldier = mock_matrix_db["soldier_a"]
    place(db_session, "CO_A_UNHELD", group=group_graph["188/53/A"])

    assert db_session.query(authz.GroupMembership).filter_by(
        user_id=soldier.id, group_id=group_graph["188/53/A"].id
    ).count() == 1
    assert visible(db_session, soldier) == {"SA100"}


def test_a_commander_sees_an_item_they_hold_outside_their_own_subtree(
    db_session, mock_matrix_db, group_graph
):
    """The holder arm is independent of the grant arm, not a fallback for the
    unprivileged.

    H1-5 made this uniform on purpose. Before, the listing replaced the holder
    filter with the hierarchy filter, so a commander could resolve an item they
    carried outside their own subtree by id through
    get_scoped_equipment_or_404 but could never find it in a listing. Nothing
    in the seed or the fixtures exercises it, which is exactly why it needs a
    test rather than an assumption.
    """
    cmdr_a = mock_matrix_db["company_cmdr_a"]
    place(db_session, "CARRIED_ABROAD", group=group_graph["188/53/B"], holder=cmdr_a)

    assert visible(db_session, cmdr_a) == {"SA100", "TA300", "CARRIED_ABROAD"}


def test_master_sees_by_grant_and_is_bounded_by_the_graph(
    db_session, mock_matrix_db, group_graph
):
    """The inversion this test was written to record, now that H1-10 arrived.

    It used to assert that is_master short-circuited before any group was
    consulted, and it was pinned against an item in a tree nobody has a grant
    over precisely because that is the only case the bypass and the root grant
    answer differently. The bypass is gone; the answer changed; the pin is what
    makes the change visible instead of invisible.

    Master and the brigade commander now return the SAME set, and neither
    reaches 920/Other. That is the point rather than a loss of privilege:
    master's sight is desc(root), and a disconnected tree is not under the
    root. Someone who needs to see it must be granted VIEW over it, which the
    model can express and a role comparison could not.
    """
    place(db_session, "UNREACHABLE", group=create_group(db_session, "920/Other"))

    inside_the_tree = {"SA100", "SB200", "TA300"}
    assert visible(db_session, mock_matrix_db["master"]) == inside_the_tree
    assert visible(db_session, mock_matrix_db["brigade_cmdr"]) == inside_the_tree, (
        "master and a root-granted commander are now indistinguishable by sight"
    )


# --- the sharp edge in the algebra -----------------------------------------

def test_a_group_absent_from_the_closure_is_unreachable_by_any_grant(
    db_session, mock_matrix_db, group_graph
):
    """The sharp edge in the algebra, stated so nobody rediscovers it.

    desc(G) includes G by way of the depth-0 self-row, and only
    rebuild_closure emits those. A Group written on its own therefore has no
    closure row at all, and a DIRECT grant over it resolves to the empty set --
    equipment in it is invisible to the person explicitly given authority over
    it. add_edge covers this for any group that gets an edge; a group created
    without one does not, and H1-6 onward must call rebuild_closure or route
    through add_edge.
    """
    user = mock_matrix_db["soldier_b"]
    stranded = authz.Unit(name="920/Stranded")     # deliberately no rebuild
    db_session.add(stranded)
    db_session.flush()
    place(db_session, "STRANDED_ITEM", group=stranded)
    grant_view(db_session, user, stranded)

    assert db_session.query(authz.GroupClosure).filter_by(
        ancestor_id=stranded.id
    ).count() == 0
    assert visible(db_session, user) == {"SB200"}, (
        "precondition for the repair below: the direct grant reaches nothing"
    )

    authz.rebuild_closure(db_session)
    db_session.commit()
    assert visible(db_session, user) == {"SB200", "STRANDED_ITEM"}


# --- get_scoped_equipment_or_404, previously untested ----------------------

def test_the_resolver_returns_an_item_inside_the_users_extent(
    db_session, mock_matrix_db, group_graph
):
    item = place(db_session, "IN_SCOPE", group=group_graph["188/53/A"])
    resolved = get_scoped_equipment_or_404(
        db_session, mock_matrix_db["company_cmdr_a"], item.id
    )
    assert resolved.id == item.id


def test_the_resolver_returns_a_held_item_from_outside_the_extent(
    db_session, mock_matrix_db, group_graph
):
    """You can always reach what you are carrying."""
    soldier = mock_matrix_db["soldier_a"]
    item = place(db_session, "CARRIED", group=group_graph["188/53/B"], holder=soldier)
    assert get_scoped_equipment_or_404(db_session, soldier, item.id).id == item.id


@pytest.mark.parametrize("case", ["out_of_scope", "nonexistent"])
def test_the_resolver_reports_404_and_not_403(
    db_session, mock_matrix_db, group_graph, case
):
    """404 for both, so the two are indistinguishable to the caller.

    403 would confirm the id exists, turning this lookup into the enumeration
    oracle it is written to avoid. Asserting the two cases agree is the whole
    point -- a 403 on one of them leaks precisely what the status code choice
    is there to withhold.
    """
    if case == "out_of_scope":
        target = place(db_session, "ELSEWHERE", group=group_graph["188/53/B"]).id
    else:
        target = 999999

    with pytest.raises(HTTPException) as excinfo:
        get_scoped_equipment_or_404(db_session, mock_matrix_db["company_cmdr_a"], target)
    assert excinfo.value.status_code == 404


def test_a_cross_company_verification_is_refused_through_the_route(
    client, db_session, mock_matrix_db, group_graph
):
    """The resolver's reason for existing, end to end.

    /verifications is the one route already wired to it, and it had no test of
    any kind. Co A's soldier writing a status onto Co B's item must be refused
    at the lookup, and no verification row may survive the attempt.
    """
    item_b = db_session.query(models.Equipment).filter_by(serial_number="SB200").one()
    before = db_session.query(models.Verification).count()

    res = client.post(
        "/verifications/",
        json={
            "equipment_id": item_b.id,
            "verification_type": "DAILY",
            "reported_status": "Functional",
        },
        headers=create_auth_header("u_soldier_a"),
    )

    assert res.status_code == 404
    assert db_session.query(models.Verification).count() == before
