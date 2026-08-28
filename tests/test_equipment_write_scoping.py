"""Where an item lands when it is created, assigned or transferred (H1-6).

H1-5 made visibility a join against extent(user, VIEW). Nothing wrote
Equipment.group_id, so an item created through the API belonged to no group and
rose to no commander -- and since create sets no holder either, it was visible
to nobody at all, including the person who had just created it.

These three routes had no coverage of any kind before this module. That is how
the gap survived: every assertion about who can see what was made against
fixture rows written directly by the fixture, never against a row the API
itself produced.

So the assertions here are deliberately about VISIBILITY rather than about the
column. Reading back group_id would pass just as well against a route that
stamped a plausible-looking wrong group; asking which commanders can now see
the item is the question the entry exists to answer.

Creation is gated as of H1-8, which changed who does the creating in this file
but not what is being asserted about where the item lands. u_tech_a is the
Company A account that holds CREATE_EQUIPMENT -- via can_add_specific_item,
which Company Tech Soldier has and Company Commander does not -- and they sit
in the same group u_soldier_a did, so every group assertion below is unmoved.
The soldier's own refusal is asserted in test_equipment_authority.py, where it
is the claim rather than a precondition.
"""
from backend import authz, models
from backend.enums import Capability
from tests.conftest import create_auth_header, create_group

# Every account whose VIEW grant contains Company A. An item created there must
# rise to all three; u_cmdr_b is the control and appears alongside each use.
COMMANDERS = ["u_cmdr_a", "u_bat_cmdr", "u_brig_cmdr"]


def serials(client, personal_number):
    res = client.get("/equipment/accessible", headers=create_auth_header(personal_number))
    assert res.status_code == 200
    return {item["serial_number"] for item in res.json()}


def create(client, personal_number, serial, **extra):
    return client.post(
        "/equipment/",
        json={"catalog_name": "M4", "serial_number": serial, **extra},
        headers=create_auth_header(personal_number),
    )


def groupless_user(db_session):
    """A user who is a member of nothing, so no group can be derived from them.

    Three tests need this and each needs it for a different route, so it lives
    here rather than in conftest: it says something about H1-6, not about the
    fixture graph.
    """
    stray = models.User(
        personal_number="u_stray", full_name="No Group", is_active_duty=True
    )
    db_session.add(stray)
    db_session.commit()
    return stray


def group_id_of(db_session, serial):
    return db_session.query(models.Equipment).filter_by(serial_number=serial).one().group_id


# --- creation ---------------------------------------------------------------

def test_a_created_item_rises_to_its_creators_commanders(client, mock_matrix_db, group_graph):
    """The whole entry in one assertion.

    Tech A sits in 188/53/A and holds no VIEW grant of any kind, so before H1-6
    this item was invisible to every account in the system -- creation writes no
    holder either, so not even to them. It must now appear for the three
    commanders whose VIEW grants contain that company, and for no one else's.
    """
    assert create(client, "u_tech_a", "NEW1").status_code == 200

    for personal_number in COMMANDERS:
        assert "NEW1" in serials(client, personal_number), personal_number
    assert "NEW1" not in serials(client, "u_cmdr_b")


def test_a_created_item_lands_in_the_creators_own_group_not_above_it(
    client, db_session, mock_matrix_db, group_graph
):
    """Created by the battalion commander, it belongs to the battalion.

    The pair matters more than either half: derive from the wrong end of the
    hierarchy and both items land in the same place, which the assertion below
    catches and a single-item test would not.
    """
    assert create(client, "u_bat_cmdr", "BAT1").status_code == 200
    assert create(client, "u_tech_a", "CO1").status_code == 200

    assert group_id_of(db_session, "BAT1") == group_graph["188/53"].id
    assert group_id_of(db_session, "CO1") == group_graph["188/53/A"].id
    assert "BAT1" not in serials(client, "u_cmdr_a"), "the company commander is BELOW the battalion"
    assert "CO1" in serials(client, "u_bat_cmdr")


def test_a_creator_who_is_a_member_of_nothing_is_refused(
    client, db_session, mock_matrix_db, group_graph
):
    """H1-8 inverted this. It used to produce an item belonging to no group.

    Before the gate, a user in no group had nothing to derive from and the route
    wrote NULL. The comment here called that documented-but-not-endorsed: an
    item visible to nobody, waiting for H1-11 to make the column reject it.
    require() rejects it now, because a group of None is reachable by no grant
    and the gate refuses rather than guessing. The API can no longer create an
    item that belongs to no group at all.

    The stray is given CREATE_EQUIPMENT on the root deliberately, so this
    isolates the ONE thing under test. Without it they would be refused for
    holding no verb anywhere, and the test would pass with the None guard
    deleted -- asserting the grant check rather than the group derivation.
    """
    stray = groupless_user(db_session)
    db_session.add(authz.Grant(
        user_id=stray.id,
        group_id=group_graph["188"].id,
        capability=Capability.CREATE_EQUIPMENT.value,
    ))
    db_session.commit()

    res = create(client, "u_stray", "STRAY1")

    assert res.status_code == 403, res.text
    assert db_session.query(models.Equipment).filter_by(serial_number="STRAY1").count() == 0


# --- the explicit override --------------------------------------------------

def test_an_override_inside_the_creators_extent_is_honoured(
    client, db_session, mock_matrix_db, group_graph
):
    """The battalion commander places an item into one of their companies.

    This is the case the override exists for: a commander equipping a
    subordinate unit rather than their own headquarters.
    """
    res = create(client, "u_bat_cmdr", "OVR1", group_id=group_graph["188/53/B"].id)

    assert res.status_code == 200
    assert group_id_of(db_session, "OVR1") == group_graph["188/53/B"].id
    assert "OVR1" in serials(client, "u_cmdr_b")
    assert "OVR1" not in serials(client, "u_cmdr_a")


def test_an_override_outside_the_creators_extent_is_refused(
    client, db_session, mock_matrix_db, group_graph
):
    """Company A's tech cannot plant an item in Company B.

    Written against the TECH rather than the commander, and the choice is the
    whole test. Company Commander holds CREATE_EQUIPMENT nowhere at all, so
    refusing them proves only that they cannot create; Company A's tech holds it
    over 188/53/A, so the only thing that can refuse them here is the gate
    reading the group they NAMED rather than the one they belong to.

    Gate the derived group instead -- the natural-looking mistake, since that is
    where the item would land if the override were ignored -- and this account
    gets 200 while every other creation test in the file still passes.
    """
    res = create(client, "u_tech_a", "STEAL1", group_id=group_graph["188/53/B"].id)

    assert res.status_code == 403
    assert db_session.query(models.Equipment).filter_by(serial_number="STEAL1").count() == 0


def test_a_user_with_no_grant_cannot_use_the_override_at_all(
    client, db_session, mock_matrix_db, group_graph
):
    """Soldier A holds no grant of any kind, so every extent of theirs is empty.

    Their own company included. Naming the group you stand in is not a way
    around needing authority over it -- membership says where you are, a grant
    says what you may do, and only the second is consulted here.
    """
    res = create(client, "u_soldier_a", "SOL1", group_id=group_graph["188/53/A"].id)

    assert res.status_code == 403
    assert db_session.query(models.Equipment).filter_by(serial_number="SOL1").count() == 0


def test_a_refused_override_leaves_no_catalog_item_behind(
    client, db_session, mock_matrix_db, group_graph
):
    """A 403 must write nothing at all, catalog rows included.

    create_equipment commits a new CatalogItem before it builds the equipment
    row. Resolving the group after that block would let a rejected request
    leave a permanent row under an attacker-chosen name -- a write performed by
    a request the route answered "denied". The ordering is the fix; this is
    what holds it in place.
    """
    before = db_session.query(models.CatalogItem).count()

    res = client.post(
        "/equipment/",
        json={
            "catalog_name": "SMUGGLED_CATALOG_NAME",
            "serial_number": "CAT1",
            "group_id": group_graph["188/53/B"].id,
        },
        headers=create_auth_header("u_cmdr_a"),
    )

    assert res.status_code == 403
    assert db_session.query(models.CatalogItem).count() == before
    assert db_session.query(models.CatalogItem).filter_by(
        name="SMUGGLED_CATALOG_NAME"
    ).count() == 0


def test_master_may_override_only_within_their_root_grant(
    client, db_session, mock_matrix_db, group_graph
):
    """H1-8 showed the difference this test was written to show.

    It used to assert that master may override into ANY group, including one
    nobody holds a grant over, because is_master short-circuited the check.
    Its own docstring predicted the change: "H1-10 turns this into a root grant
    and this test is what will show the difference." H1-8 is where it showed --
    the equipment routes compare no role at all now, and master's authority is
    the every-capability grant seeded on the root.

    Both halves, because either alone is weak. Inside the root's subtree master
    still places items anywhere, so this is not a regression in reach; outside
    it -- a group with no edge to 188 and therefore in no extent -- master is
    refused exactly like anyone else. A surviving bypass passes the first and
    fails the second.
    """
    outside = create_group(db_session, "920/Other")

    assert create(client, "u_master", "MST1", group_id=group_graph["188/53/B"].id).status_code == 200
    assert group_id_of(db_session, "MST1") == group_graph["188/53/B"].id

    res = create(client, "u_master", "MST2", group_id=outside.id)
    assert res.status_code == 403, res.text
    assert db_session.query(models.Equipment).filter_by(serial_number="MST2").count() == 0


# --- assign_owner -----------------------------------------------------------

def test_assigning_ownership_across_companies_moves_the_item(
    client, db_session, mock_matrix_db, group_graph
):
    """SA100 belongs to Company A. Assigned to Company B's soldier, it is Company B's.

    Both halves are load-bearing. Gaining it without losing it means two
    commanders can see one item; losing it without gaining it means nobody can.
    """
    item = db_session.query(models.Equipment).filter_by(serial_number="SA100").one()
    soldier_b = mock_matrix_db["soldier_b"]

    res = client.post(
        "/equipment/assign_owner/",
        json={"equipment_id": item.id, "owner_id": soldier_b.id},
        headers=create_auth_header("u_bat_cmdr"),
    )

    assert res.status_code == 200
    assert group_id_of(db_session, "SA100") == group_graph["188/53/B"].id
    assert "SA100" in serials(client, "u_cmdr_b")
    assert "SA100" not in serials(client, "u_cmdr_a")


def test_assigning_ownership_to_a_nonexistent_user_returns_404(
    client, db_session, mock_matrix_db, group_graph
):
    """DATA-M2, for this route only.

    This route used to write req.owner_id into two foreign keys with no lookup,
    so a nonexistent id produced a dangling reference and a 200. The ticket
    stays open for its other sites.
    """
    item = db_session.query(models.Equipment).filter_by(serial_number="SA100").one()

    res = client.post(
        "/equipment/assign_owner/",
        json={"equipment_id": item.id, "owner_id": 999999},
        headers=create_auth_header("u_bat_cmdr"),
    )

    assert res.status_code == 404
    db_session.refresh(item)
    assert item.owner_user_id != 999999
    assert item.group_id == group_graph["188/53/A"].id, "a refused assignment moved nothing"


def test_assigning_ownership_to_a_user_in_no_group_leaves_the_group_unchanged(
    client, db_session, mock_matrix_db, group_graph
):
    """Unchanged, not cleared -- the assign_owner twin of the transfer case below.

    Found by mutation: removing the None guard here left the whole suite green,
    because the transfer path had this test and this path did not. Writing None
    would take the item out of every commander's sight, which is the defect
    H1-6 exists to close, so the item stays where it was.
    """
    stray = groupless_user(db_session)
    item = db_session.query(models.Equipment).filter_by(serial_number="SA100").one()

    res = client.post(
        "/equipment/assign_owner/",
        json={"equipment_id": item.id, "owner_id": stray.id},
        headers=create_auth_header("u_bat_cmdr"),
    )

    assert res.status_code == 200
    db_session.refresh(item)
    assert item.owner_user_id == stray.id
    assert item.group_id == group_graph["188/53/A"].id
    assert "SA100" in serials(client, "u_cmdr_a")


# --- transfer ---------------------------------------------------------------

def test_transferring_possession_across_companies_moves_the_item(
    client, db_session, mock_matrix_db, group_graph
):
    item = db_session.query(models.Equipment).filter_by(serial_number="SA100").one()
    soldier_b = mock_matrix_db["soldier_b"]

    res = client.post(
        "/equipment/transfer",
        json={"equipment_id": item.id, "to_holder_id": soldier_b.id},
        headers=create_auth_header("u_bat_cmdr"),
    )

    assert res.status_code == 200
    assert group_id_of(db_session, "SA100") == group_graph["188/53/B"].id
    assert "SA100" in serials(client, "u_cmdr_b")
    assert "SA100" not in serials(client, "u_cmdr_a")


def test_transferring_to_a_location_leaves_the_item_in_its_unit(
    client, db_session, mock_matrix_db, group_graph
):
    """The branch that would break silently.

    An item is AT a location but still BELONGS TO its unit. This branch clears
    holder_user_id, so the holder arm of the scoping predicate stops applying
    and group_id becomes the only thing keeping the item visible to anyone.
    Re-deriving here -- from a holder who no longer exists -- would strand every
    item ever put in an armoury, and no other test in the suite would notice.
    """
    item = db_session.query(models.Equipment).filter_by(serial_number="SA100").one()

    res = client.post(
        "/equipment/transfer",
        json={"equipment_id": item.id, "to_location": "Main Armory"},
        headers=create_auth_header("u_bat_cmdr"),
    )

    assert res.status_code == 200
    db_session.refresh(item)
    assert item.holder_user_id is None, "precondition: the holder arm no longer applies"
    assert item.group_id == group_graph["188/53/A"].id
    assert "SA100" in serials(client, "u_cmdr_a")
    assert "SA100" in serials(client, "u_bat_cmdr")


def test_transferring_to_a_user_in_no_group_leaves_the_group_unchanged(
    client, db_session, mock_matrix_db, group_graph
):
    """Unchanged, not cleared.

    A target with no membership yields no group. Writing that None would make
    the item invisible to every commander -- the exact defect H1-6 closes --
    so the item stays where it was and its old commanders keep seeing it. It
    also cannot raise: this sits inside the try block whose broad except would
    re-wrap an HTTPException as a 500 (DATA-H6).
    """
    stray = groupless_user(db_session)
    item = db_session.query(models.Equipment).filter_by(serial_number="SA100").one()

    res = client.post(
        "/equipment/transfer",
        json={"equipment_id": item.id, "to_holder_id": stray.id},
        headers=create_auth_header("u_bat_cmdr"),
    )

    assert res.status_code == 200
    db_session.refresh(item)
    assert item.holder_user_id == stray.id
    assert item.group_id == group_graph["188/53/A"].id
    assert "SA100" in serials(client, "u_cmdr_a")


def test_a_transfer_within_the_same_company_changes_nothing(
    client, db_session, mock_matrix_db, group_graph
):
    """The ordinary case, and the one an existing test already depends on.

    test_commanders_can_transfer moves SA100 to Company A's own commander. If
    re-derivation were not a no-op there, that test would have started failing
    for a reason unrelated to what it asserts.
    """
    item = db_session.query(models.Equipment).filter_by(serial_number="SA100").one()

    res = client.post(
        "/equipment/transfer",
        json={"equipment_id": item.id, "to_holder_id": mock_matrix_db["company_cmdr_a"].id},
        headers=create_auth_header("u_cmdr_a"),
    )

    assert res.status_code == 200
    assert group_id_of(db_session, "SA100") == group_graph["188/53/A"].id
    assert serials(client, "u_cmdr_a") == {"SA100", "TA300"}


# --- the round trip ---------------------------------------------------------

def test_an_item_is_visible_to_exactly_one_chain_at_every_step(
    client, db_session, mock_matrix_db, group_graph
):
    """Create, transfer out, transfer back -- and at no point is it in both.

    Each step is asserted from both sides. A route that granted the new
    commander sight without removing the old one would pass every "can see it"
    assertion in this module and fail only here.
    """
    assert create(client, "u_tech_a", "TRIP1").status_code == 200
    assert "TRIP1" in serials(client, "u_cmdr_a")
    assert "TRIP1" not in serials(client, "u_cmdr_b")

    item = db_session.query(models.Equipment).filter_by(serial_number="TRIP1").one()

    def hand_to(user_key):
        res = client.post(
            "/equipment/transfer",
            json={"equipment_id": item.id, "to_holder_id": mock_matrix_db[user_key].id},
            headers=create_auth_header("u_master"),
        )
        assert res.status_code == 200, res.text

    hand_to("soldier_b")
    assert "TRIP1" in serials(client, "u_cmdr_b")
    assert "TRIP1" not in serials(client, "u_cmdr_a")

    hand_to("soldier_a")
    assert "TRIP1" in serials(client, "u_cmdr_a")
    assert "TRIP1" not in serials(client, "u_cmdr_b")


def test_the_created_item_is_reachable_through_the_report_as_well(
    client, db_session, mock_matrix_db, group_graph
):
    """The second consumer of the shared predicate.

    /reports/query and /equipment/accessible run the same scoping helper since
    H1-5, and test_reports_scoping pins that they agree. This checks the
    agreement survives a row the API created rather than one the fixture wrote.
    """
    assert create(client, "u_tech_a", "RPT1").status_code == 200

    res = client.get("/reports/query", headers=create_auth_header("u_cmdr_a"))
    assert res.status_code == 200
    assert "RPT1" in {row["serial_number"] for row in res.json()}

    res = client.get("/reports/query", headers=create_auth_header("u_cmdr_b"))
    assert "RPT1" not in {row["serial_number"] for row in res.json()}


def test_creation_writes_no_legacy_path_string(client, db_session, mock_matrix_db, group_graph):
    """The Phase 2 gate, from the writing side.

    H1-11 dropped Equipment.unit_hierarchy. A write path that started
    populating it would resurrect the representation this phase exists to
    remove, and nothing else in the suite would object.

    This asserted `item.unit_hierarchy is None` while the column still
    existed. Now it asserts the attribute is absent, which is the stronger
    claim and the one that keeps failing if anyone re-adds the column: a
    re-added column would be None on a fresh row and pass the old check.
    """
    assert create(client, "u_tech_a", "PATH1").status_code == 200
    item = db_session.query(models.Equipment).filter_by(serial_number="PATH1").one()

    assert not hasattr(item, "unit_hierarchy")
    assert not hasattr(item, "unit_path")
    assert item.group_id is not None


def test_the_derivation_reads_membership_and_not_a_grant(
    client, db_session, mock_matrix_db, group_graph
):
    """Brigade commander: VIEW over the whole force, membership in the root.

    Give them a second membership in Company A and their items must land in
    Company A, not at the root their grant covers. Deriving from the grant
    instead would put every item they create where the entire force can see it,
    and against this fixture that mistake is invisible -- their grant and their
    membership are the same group until this test moves one of them.
    """
    db_session.add(authz.GroupMembership(
        user_id=mock_matrix_db["brigade_cmdr"].id, group_id=group_graph["188/53/A"].id
    ))
    db_session.commit()

    assert create(client, "u_brig_cmdr", "MEM1").status_code == 200

    assert group_id_of(db_session, "MEM1") == group_graph["188/53/A"].id
    assert "MEM1" in serials(client, "u_cmdr_a")
    assert set(db_session.execute(
        authz.extent(mock_matrix_db["brigade_cmdr"].id, Capability.VIEW)
    ).scalars()) == {group_graph[name].id for name in group_graph}
