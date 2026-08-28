"""H1-10: the two verbs that have no group, and the last role comparison.

`setup.py`'s fault-type routes and `users.py`'s personnel routes had no
coverage of any kind before this module, which is a large part of how a role
comparison survived nine entries of a cutover designed to remove exactly that.

Three things are new here and each is asserted from both sides:

  1. **Global authority.** CatalogItem and FaultType have no group and never
     will -- they are one shared namespace for the whole force. Through H1-9
     that was read as putting them outside the algebra. It does not: a resource
     belonging to everyone is scoped by the node that MEANS everyone, and the
     graph has one. `require_global` asks the verb over the root.

  2. **Every root, not any.** On the seeded graph there is exactly one root and
     the two readings coincide, so the rule is invisible until someone adds a
     second tree. Two tests below add one.

  3. **Master is now only its grants -- for sight as well as for action.**
     `is_master` is deleted. That is the difference between this entry and
     H1-8/H1-9, which left visibility on the role.

Refusals are asserted before permitted calls wherever a permitted call mutates
the state a refusal depends on -- the same discipline as test_status_authority.
"""
import pytest

from backend import authz, models
from backend.enums import Capability
from tests.conftest import create_auth_header, create_group, revoke

# --- helpers ---------------------------------------------------------------


def create_user(client, who, db, personal_number="NEW-1", group_id=None):
    if group_id is None:
        group_id = db.query(authz.Group).filter_by(name="188/53/A").one().id
    return client.post(
        "/users/",
        json={
            "personal_number": personal_number,
            "full_name": "Recruit",
            "password": "irrelevant-but-required",
            "group_id": group_id,
        },
        headers=create_auth_header(who),
    )


def reassign_group(client, who, target_id, group_id):
    return client.put(
        f"/users/{target_id}/group",
        json={"group_id": group_id},
        headers=create_auth_header(who),
    )


def create_fault(client, who, name):
    # `name` is a query parameter, not a body -- DATA-M5, untouched here.
    return client.post(f"/setup/fault_types?name={name}", headers=create_auth_header(who))


def approve_fault(client, who, fault_id):
    return client.put(f"/setup/fault_types/{fault_id}/approve", headers=create_auth_header(who))


def delete_fault(client, who, fault_id):
    return client.delete(f"/setup/fault_types/{fault_id}", headers=create_auth_header(who))


def pending_faults(client, who):
    return client.get("/setup/fault_types/pending", headers=create_auth_header(who))


def a_fault(db, name="Cracked Housing", pending=True):
    fault = models.FaultType(name=name, is_pending=pending)
    db.add(fault)
    db.commit()
    db.refresh(fault)
    return fault


# --- MANAGE_PERSONNEL, which replaced the role comparison ------------------


def test_personnel_routes_refuse_everyone_without_the_verb(
    client, db_session, mock_matrix_db
):
    """Both routes verify_admin_access used to guard, from the refusing side.

    company_cmdr_a commands a company, holds four verbs, and is refused both:
    MANAGE_PERSONNEL is held by Master alone. The personnel table belongs to
    no unit, so commanding one buys nothing here.
    """
    target = mock_matrix_db["soldier_a"]
    other_group = db_session.query(authz.Group).filter_by(name="188/53/B").one()

    assert create_user(client, "u_cmdr_a", db_session).status_code == 403
    assert reassign_group(client, "u_cmdr_a", target.id, other_group.id).status_code == 403

    db_session.refresh(target)
    assert target.group.name != "188/53/B"
    assert db_session.query(models.User).filter_by(personal_number="NEW-1").first() is None


def test_the_personnel_verb_holder_acts(client, db_session, mock_matrix_db):
    """The permitted side, so the refusals above are not passing vacuously.

    A gate that refused everyone would satisfy every assertion in the test
    before this one.
    """
    assert create_user(client, "u_master", db_session).status_code == 200
    assert db_session.query(models.User).filter_by(personal_number="NEW-1").first() is not None

    target = mock_matrix_db["soldier_a"]
    other_group = db_session.query(authz.Group).filter_by(name="188/53/B").one()
    assert reassign_group(client, "u_master", target.id, other_group.id).status_code == 200
    db_session.refresh(target)
    assert target.group.name == "188/53/B"


def test_create_user_refuses_an_unknown_group(client, db_session, mock_matrix_db):
    """The H1-6 debt this entry closes: group_id is required, and validated.

    An id that names no row must not silently create an unplaced account --
    that is exactly the gap create_user carried since H1-6, closed here by
    requiring and checking the id rather than defaulting it away.
    """
    res = create_user(client, "u_master", db_session, group_id=999999)
    assert res.status_code == 404
    assert db_session.query(models.User).filter_by(personal_number="NEW-1").first() is None


def test_create_user_writes_exactly_one_membership(client, db_session, mock_matrix_db):
    """The new account is placed, not merely created."""
    group = db_session.query(authz.Group).filter_by(name="188/53/A").one()
    assert create_user(client, "u_master", db_session, group_id=group.id).status_code == 200

    new_user = db_session.query(models.User).filter_by(personal_number="NEW-1").one()
    memberships = db_session.query(authz.GroupMembership).filter_by(user_id=new_user.id).all()
    assert [m.group_id for m in memberships] == [group.id]


def test_reassigning_a_group_replaces_rather_than_adds(client, db_session, mock_matrix_db):
    """One membership row per user, always -- the same shape create_user writes.

    Without the delete before the insert, reassigning twice would leave a
    user standing in two groups at once, which nothing downstream expects
    (models.User.group takes the first membership on the assumption that
    there is exactly one).
    """
    target = mock_matrix_db["soldier_a"]
    group_b = db_session.query(authz.Group).filter_by(name="188/53/B").one()
    root = db_session.query(authz.Group).filter_by(name="188").one()

    assert reassign_group(client, "u_master", target.id, group_b.id).status_code == 200
    assert reassign_group(client, "u_master", target.id, root.id).status_code == 200

    memberships = db_session.query(authz.GroupMembership).filter_by(user_id=target.id).all()
    assert [m.group_id for m in memberships] == [root.id]


# --- MANAGE_CATALOG, and the claim it corrects -----------------------------


def test_catalog_routes_refuse_everyone_without_the_verb(
    client, db_session, mock_matrix_db
):
    """The four Profile reads that were the last ones in any router.

    bat_cmdr is the sharp choice here: they hold five verbs including
    CREATE_EQUIPMENT and RESOLVE_FAULT, and command the whole battalion. They
    do not hold MANAGE_CATALOG, because can_add_category is on Master and
    Brigade Tech Commander only. Authority over units is not authority over the
    vocabulary every unit shares.
    """
    fault = a_fault(db_session)

    assert pending_faults(client, "u_bat_cmdr").status_code == 403
    assert approve_fault(client, "u_bat_cmdr", fault.id).status_code == 403
    assert delete_fault(client, "u_bat_cmdr", fault.id).status_code == 403

    db_session.refresh(fault)
    assert fault.is_pending is True
    assert db_session.query(models.FaultType).filter_by(id=fault.id).first() is not None


def test_the_catalog_verb_holder_acts(client, db_session, mock_matrix_db):
    """Brigade Tech Commander holds it, and is not a master.

    Worth asserting with this account rather than master: it shows the verb is
    the thing being consulted, not a rank, and it is the only account that
    separates MANAGE_CATALOG from MANAGE_PERSONNEL from the holding side.
    """
    fault = a_fault(db_session)

    assert pending_faults(client, "u_brig_cmdr").status_code == 200
    assert approve_fault(client, "u_brig_cmdr", fault.id).status_code == 200
    db_session.refresh(fault)
    assert fault.is_pending is False

    assert delete_fault(client, "u_brig_cmdr", fault.id).status_code == 200
    assert db_session.query(models.FaultType).filter_by(id=fault.id).first() is None


def test_the_two_global_verbs_are_not_interchangeable(client, db_session, mock_matrix_db):
    """One account, two namespaces, opposite answers.

    Brigade Tech Commander holds MANAGE_CATALOG and not MANAGE_PERSONNEL. A gate
    asking merely whether SOME grant exists on the root -- what dropping the
    capability filter produces, and what reads as a harmless simplification --
    answers 200 to both halves and passes every other test in this file.
    """
    fault = a_fault(db_session)

    assert approve_fault(client, "u_brig_cmdr", fault.id).status_code == 200
    assert create_user(client, "u_brig_cmdr", db_session).status_code == 403

    # And the converse: master holds both, so the asymmetry is the account's.
    assert create_user(client, "u_master", db_session, personal_number="NEW-2").status_code == 200


# --- every root, not any ---------------------------------------------------


def test_a_second_tree_defeats_authority_held_over_only_one(
    client, db_session, mock_matrix_db
):
    """The entry's one genuinely new rule, and nothing else reaches it.

    require_global asks the verb over EVERY root. The fixture has exactly one,
    so the every/any distinction is invisible until a second tree exists -- and
    a rule nothing can distinguish is a rule waiting to be chosen wrong by
    whoever first adds one.

    A shared namespace is shared by all of it. Holding MANAGE_CATALOG over one
    tree while another tree exists is authority over that tree, not over the
    vocabulary both of them use. Master's grants are untouched; what changed is
    that the graph grew a top they do not stand on.
    """
    fault = a_fault(db_session)
    assert approve_fault(client, "u_master", fault.id).status_code == 200

    create_group(db_session, "920/Independent")

    second = a_fault(db_session, name="Second Fault")
    assert approve_fault(client, "u_master", second.id).status_code == 403
    assert create_user(client, "u_master", db_session).status_code == 403

    db_session.refresh(second)
    assert second.is_pending is True


def test_authority_over_the_new_tree_restores_it(client, db_session, mock_matrix_db):
    """The other half, so the test above is not merely proving that adding a
    group breaks things.

    Granted over both roots, the same account acts again. That is what makes the
    refusal above a statement about coverage of the graph rather than a bug.
    """
    other = create_group(db_session, "920/Independent")
    fault = a_fault(db_session)
    assert approve_fault(client, "u_master", fault.id).status_code == 403

    db_session.add(authz.Grant(
        user_id=mock_matrix_db["master"].id,
        group_id=other.id,
        capability=Capability.MANAGE_CATALOG.value,
    ))
    db_session.commit()

    assert approve_fault(client, "u_master", fault.id).status_code == 200


def test_a_global_verb_granted_below_the_root_authorises_nothing(
    client, db_session, mock_matrix_db
):
    """Position is the whole of what a grant means, including for these two.

    Found by mutation: handing company_cmdr_a MANAGE_PERSONNEL over their own
    company, and bat_cmdr MANAGE_CATALOG over the battalion, left the entire
    suite green. Neither mutation changed any behaviour -- which is the rule
    working, not a hole -- because require_global asks over the ROOT and a
    grant at 188/53/A does not cover 188. The tests were right and the
    mutations were toothless.

    But nothing said so. This does, from both sides: the same account, the
    same verb, refused at the company and permitted at the root. It is the
    exact inverse of how every other verb behaves -- authority points DOWN, so
    a grant on a company reaches that company -- and a global verb is the one
    case where holding it somewhere real still means holding it nowhere.
    """
    cmdr = mock_matrix_db["company_cmdr_a"]
    company = db_session.query(authz.Group).filter_by(name="188/53/A").one()
    root = db_session.query(authz.Group).filter_by(name="188").one()

    db_session.add(authz.Grant(
        user_id=cmdr.id,
        group_id=company.id,
        capability=Capability.MANAGE_PERSONNEL.value,
    ))
    db_session.commit()

    assert authz.may(db_session, cmdr.id, Capability.MANAGE_PERSONNEL, company.id) is True, (
        "the premise: the grant is real and reaches their own company"
    )
    assert create_user(client, "u_cmdr_a", db_session).status_code == 403

    db_session.add(authz.Grant(
        user_id=cmdr.id,
        group_id=root.id,
        capability=Capability.MANAGE_PERSONNEL.value,
    ))
    db_session.commit()

    assert create_user(client, "u_cmdr_a", db_session).status_code == 200


def test_an_empty_graph_grants_nobody_global_authority(
    client, db_session, mock_matrix_db
):
    """Fail closed where all() would fail open.

    With no roots there is no node standing for the whole force, so nobody holds
    authority over it. Written because the natural implementation --
    all(may(...) for root in roots) -- returns True for an empty sequence, which
    would hand every authenticated user the catalog and the personnel table on a
    database whose graph had not been built yet. That is precisely the state a
    fresh deployment is in before the seed runs.
    """
    fault = a_fault(db_session)
    # The equipment goes with the graph. This used to NULL group_id instead,
    # so the rows could let go of the groups before the groups were deleted;
    # H1-11 made group_id NOT NULL and that half-state stopped being
    # representable -- correctly, since an item in no group is visible to
    # nobody. No groups AND no equipment is what a database whose graph was
    # never built actually looks like, which is the state under test.
    #
    # The fault survives: FaultType names a kind of fault and references no
    # equipment, so the 403 below is still asked about a row that exists.
    db_session.query(models.Equipment).delete(synchronize_session=False)
    db_session.query(authz.GroupEdge).delete(synchronize_session=False)
    db_session.query(authz.GroupClosure).delete(synchronize_session=False)
    db_session.query(authz.Grant).delete(synchronize_session=False)
    db_session.query(authz.GroupMembership).delete(synchronize_session=False)
    db_session.query(authz.Group).delete(synchronize_session=False)
    db_session.commit()

    assert authz.root_groups(db_session) == []
    assert authz.may_global(db_session, mock_matrix_db["master"].id, Capability.MANAGE_CATALOG) is False

    assert approve_fault(client, "u_master", fault.id).status_code == 403
    # No group survives the wipe above either, so a literal id stands in --
    # require_global refuses before create_user ever looks the group up.
    assert create_user(client, "u_master", db_session, group_id=1).status_code == 403


# --- is_pending: a question, not a gate ------------------------------------


@pytest.mark.parametrize(
    "who,expected_pending",
    [("u_master", False), ("u_soldier_a", True)],
    ids=["holder", "non_holder"],
)
def test_proposing_vocabulary_is_open_and_skipping_review_is_not(
    client, db_session, mock_matrix_db, who, expected_pending
):
    """create_fault_type is deliberately ungated, and that is not an oversight.

    It is the front door of the approval workflow: anyone may PROPOSE a fault
    type, and holding MANAGE_CATALOG is what lets the proposal skip review. So
    the verb is consulted with may_global rather than require_global, and a "no"
    narrows the write instead of refusing it -- exactly the shape report_fault's
    is_pending took in H1-9.

    Both callers get 200. The difference is one column, which is why a test that
    only checked status codes would see nothing here.
    """
    res = create_fault(client, who, "NOVEL-TYPE")
    assert res.status_code == 200, res.text

    fault = db_session.query(models.FaultType).filter_by(name="NOVEL-TYPE").one()
    assert fault.is_pending is expected_pending


def test_the_group_list_is_gated_on_the_personnel_verb(
    client, db_session, mock_matrix_db
):
    """GET /groups (H1-12 -- replaces the old GET /profiles) needs the same gate.

    It enumerates the whole org chart, which is what an admin needs to place
    anyone anywhere -- not a disclosure about any particular user, but a map
    of the structure the system can assign people into. Assigning one of
    these is already a personnel act (users.update_user_group). Same
    authority, so the same verb.

    Gated rather than scoped, unlike the four listings in SEC-H5: the
    personnel table belongs to no unit, so there is no scope to apply. That
    is the same reason require_global exists at all.
    """
    res = client.get("/groups", headers=create_auth_header("u_master"))
    assert res.status_code == 200
    assert len(res.json()) > 0

    for who in ("u_cmdr_a", "u_brig_cmdr", "u_soldier_a"):
        res = client.get("/groups", headers=create_auth_header(who))
        assert res.status_code == 403, who


# --- the deleted mechanism --------------------------------------------------
#
# test_the_master_role_by_itself_now_authorises_nothing used to live here: it
# hand-set an ordinary soldier's `role` column to MASTER and asserted that
# every route still refused them, proving the comparison verify_admin_access
# used to make was gone from every router. H1-12 drops the `role` column
# entirely, so the premise -- a role value existing that could theoretically
# be compared -- is no longer constructible. The guarantee this test proved
# at runtime is now structural: there is no column left to reinstate a
# comparison against without a schema change, which migration review would
# catch on its own. The refusal coverage itself is not lost -- soldier_a
# holding no verb is exercised by test_catalog_routes_refuse_everyone_without
# _the_verb and test_a_global_verb_confers_no_sight_of_equipment below.


def test_revoking_the_global_verbs_refuses_the_account_that_held_them(
    client, db_session, mock_matrix_db
):
    """Revocation takes effect, not merely non-possession.

    Every other refusal in this file is about an account that never held the
    verb. This is the one case where an account that COULD act loses that
    ability -- proving authority is read live from the grant table on every
    request rather than cached or inferred from anything else about the
    account.
    """
    fault = a_fault(db_session)
    assert approve_fault(client, "u_master", fault.id).status_code == 200

    revoke(db_session, mock_matrix_db["master"],
           Capability.MANAGE_CATALOG, Capability.MANAGE_PERSONNEL)

    assert approve_fault(client, "u_master", fault.id).status_code == 403
    assert create_user(client, "u_master", db_session).status_code == 403


def test_a_global_verb_confers_no_sight_of_equipment(
    client, db_session, mock_matrix_db, group_graph
):
    """The exclusion in the derived-VIEW rule, which nothing else reaches.

    H1-10.5 derives VIEW rows from the verbs that act on equipment in a group,
    and deliberately excludes MANAGE_CATALOG and MANAGE_PERSONNEL: those
    authorise vocabulary and people, not equipment. Fold them in and anyone who
    can create a user can see every item in the force.

    Mutation found that exclusion unobservable -- every seeded holder of a
    global verb also holds VIEW on the root, so adding them to the derivation
    changed nothing. The case has to be constructed: a soldier granted
    MANAGE_PERSONNEL over the root and nothing else.

    They can administer personnel, which the roster assertion below confirms
    is real authority and not a no-op grant, and they still see exactly the one
    item they carry.
    """
    soldier = mock_matrix_db["soldier_a"]
    db_session.add(authz.Grant(
        user_id=soldier.id,
        group_id=group_graph["188"].id,
        capability=Capability.MANAGE_PERSONNEL.value,
    ))
    db_session.commit()

    # The grant is real: it reaches the personnel routes.
    res = client.post("/users/", json={
        "personal_number": "NEW-9", "full_name": "Recruit",
        "password": "irrelevant", "group_id": group_graph["188/53/A"].id,
    }, headers=create_auth_header("u_soldier_a"))
    assert res.status_code == 200, res.text

    # And it buys no sight of equipment whatsoever.
    res = client.get("/equipment/accessible", headers=create_auth_header("u_soldier_a"))
    assert res.status_code == 200
    assert {i["serial_number"] for i in res.json()} == {"SA100"}
