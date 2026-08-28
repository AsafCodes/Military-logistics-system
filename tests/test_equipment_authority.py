"""Who may write to equipment, and what a refusal discloses (H1-8).

test_equipment_write_scoping.py asks where an item LANDS. This module asks
whether the request should have been allowed to land it anywhere, which until
H1-8 was decided by three mechanisms that were not the group model: a profile
boolean, a hardcoded allowlist of profile NAMES, and a role string comparison.

Two properties are asserted here that no single route can demonstrate on its
own, and both are the reason the entry exists rather than pleasant extras:

  * **404 before 403.** Every gated route resolves the item inside the caller's
    own VIEW extent first, so an id they cannot see is indistinguishable from
    one that does not exist. A 403 therefore reaches only someone who could
    already list the row. Asserted per route against accounts that produce both
    codes, because a gate that always answers the same one satisfies either
    half alone.

  * **The verbs do not stand in for one another.** Company A contains one
    account holding TRANSFER and not CREATE_EQUIPMENT, and another holding
    CREATE_EQUIPMENT and not TRANSFER. Both sit in the same group, so a route
    reading the wrong verb -- or a gate that checks merely that SOME grant
    exists -- passes every other test in the suite and fails here.

The fixture's grants are the ones seeded from profiles.py, so the accounts are
named by what their profile actually carries rather than by rank:

    company_cmdr_a   VIEW + TRANSFER over 188/53/A, and no CREATE_EQUIPMENT
                     (Company Commander lacks can_add_specific_item)
    company_tech_a   CREATE_EQUIPMENT over 188/53/A, and nothing else
                     (Company Tech Soldier lacks can_change_assignment_others)
    soldier_a        no grant at all; holds SA100, so they can SEE it
    bat_cmdr         all three verbs over 188/53
    master           all three verbs over 188, plus the is_master VISIBILITY
                     short-circuit that H1-10 removes
"""
import pytest

from backend import authz, models
from backend.enums import Capability
from tests.conftest import create_auth_header


def transfer(client, personal_number, equipment_id, to_holder_id):
    return client.post(
        "/equipment/transfer",
        json={"equipment_id": equipment_id, "to_holder_id": to_holder_id},
        headers=create_auth_header(personal_number),
    )


def assign(client, personal_number, equipment_id, owner_id):
    return client.post(
        "/equipment/assign_owner/",
        json={"equipment_id": equipment_id, "owner_id": owner_id},
        headers=create_auth_header(personal_number),
    )


def create(client, personal_number, serial, **extra):
    return client.post(
        "/equipment/",
        json={"catalog_name": "M4", "serial_number": serial, **extra},
        headers=create_auth_header(personal_number),
    )


def item_named(db_session, serial):
    return db_session.query(models.Equipment).filter_by(serial_number=serial).one()


# --- transfer ---------------------------------------------------------------

def test_a_transfer_grant_over_the_items_group_permits(client, db_session, mock_matrix_db):
    """The ordinary case, and the baseline the two refusals below are measured against."""
    item = item_named(db_session, "SA100")

    res = transfer(client, "u_cmdr_a", item.id, mock_matrix_db["company_tech_a"].id)

    assert res.status_code == 200, res.text
    db_session.refresh(item)
    assert item.holder_user_id == mock_matrix_db["company_tech_a"].id


def test_seeing_an_item_without_a_transfer_grant_is_403(client, db_session, mock_matrix_db):
    """Soldier A holds SA100, so the holder arm resolves it for them.

    They hold no grant anywhere. This is the case the pair of status codes
    exists to distinguish: the item is theirs to see and not theirs to move.
    """
    item = item_named(db_session, "SA100")

    res = transfer(client, "u_soldier_a", item.id, mock_matrix_db["soldier_b"].id)

    assert res.status_code == 403, res.text
    db_session.refresh(item)
    assert item.holder_user_id == mock_matrix_db["soldier_a"].id


def test_transferring_an_item_in_another_company_is_404_not_403(
    client, db_session, mock_matrix_db, group_graph
):
    """The hole H1-8 closes on the way past, which no ticket names.

    Both routes used to resolve equipment with a bare filter on the raw id and
    an existence check -- no scope at all -- and Company A's commander holds
    can_change_assignment_others, so they could reassign Company B's item by
    guessing its number. That is SEC-H6's shape at a site SEC-H6 does not list.

    404 rather than 403 is the whole point: a 403 here would confirm the id
    exists, which is exactly the enumeration answer the resolver refuses to
    give.
    """
    item = item_named(db_session, "SB200")

    res = transfer(client, "u_cmdr_a", item.id, mock_matrix_db["soldier_a"].id)

    assert res.status_code == 404, res.text
    db_session.refresh(item)
    assert item.holder_user_id == mock_matrix_db["soldier_b"].id
    assert item.group_id == group_graph["188/53/B"].id, "a refused transfer moved the item"


def test_a_refused_transfer_writes_no_transaction_log(client, db_session, mock_matrix_db):
    """A denial must leave no trace of an event that did not happen.

    transfer_equipment writes a TransactionLog on the way through. Gating after
    the log rather than before it would record a HANDOVER for every attempt,
    including the refused ones, and the item's history is what an investigation
    reads.
    """
    item = item_named(db_session, "SA100")
    before = db_session.query(models.TransactionLog).count()

    assert transfer(client, "u_soldier_a", item.id, mock_matrix_db["soldier_b"].id).status_code == 403

    assert db_session.query(models.TransactionLog).count() == before


# --- assign_owner -----------------------------------------------------------

def test_assign_owner_answers_the_same_three_ways(client, db_session, mock_matrix_db):
    """The sibling route, gated on the same verb, in one test.

    Written together rather than as three because the claim is that the two
    routes AGREE. Split apart, one could drift to a different verb or a
    different order and each half would still look reasonable on its own.

    The permitted call comes LAST on purpose, and the ordering is load-bearing
    rather than tidy. A successful assignment rewrites owner, holder and group,
    so running it first takes SA100 out of soldier_a's hands -- and soldier_a
    sees SA100 only BECAUSE they hold it. The 403 this test wants would quietly
    become a 404 about a different fact.
    """
    own = item_named(db_session, "SA100")
    elsewhere = item_named(db_session, "SB200")
    target = mock_matrix_db["company_tech_a"].id

    assert assign(client, "u_cmdr_a", elsewhere.id, target).status_code == 404
    assert assign(client, "u_soldier_a", own.id, target).status_code == 403
    assert assign(client, "u_cmdr_a", own.id, target).status_code == 200


# --- creation ---------------------------------------------------------------

def test_creation_requires_the_creation_verb(client, db_session, mock_matrix_db):
    """Company A's tech holds CREATE_EQUIPMENT; Company A's soldier holds nothing.

    Before H1-8 this route had no authorization gate of any kind and both
    succeeded. It is the one denial in this entry that is genuinely new rather
    than a replacement, which is why it is asserted from both sides.
    """
    assert create(client, "u_tech_a", "AUTH1").status_code == 200
    assert create(client, "u_soldier_a", "AUTH2").status_code == 403
    assert db_session.query(models.Equipment).filter_by(serial_number="AUTH2").count() == 0


def test_a_refused_creation_leaves_no_catalog_row(client, db_session, mock_matrix_db):
    """The catalog block commits before the equipment row is built.

    test_equipment_write_scoping pins this for the OVERRIDE arm. The gate added
    here is a second way to reach a 403 on this route, and it has to be ordered
    ahead of the same commit -- otherwise a caller with no verb at all can still
    write a permanent row under a name of their choosing.
    """
    before = db_session.query(models.CatalogItem).count()

    res = client.post(
        "/equipment/",
        json={"catalog_name": "SMUGGLED_BY_UNGRANTED", "serial_number": "AUTH3"},
        headers=create_auth_header("u_soldier_a"),
    )

    assert res.status_code == 403, res.text
    assert db_session.query(models.CatalogItem).count() == before


# --- the verbs do not stand in for one another ------------------------------

def test_two_accounts_in_one_group_hold_opposite_verbs(client, db_session, mock_matrix_db):
    """The sharpest test in the module, and the reason the fixture looks odd.

    Company A contains a commander with TRANSFER and no CREATE_EQUIPMENT, and a
    tech with CREATE_EQUIPMENT and no TRANSFER. Same group, opposite verbs. A
    gate that checked "does this user hold ANY grant over the group" -- which is
    what dropping the capability filter from extent() produces, and which looks
    like a harmless simplification -- answers 200 to all four of these.

    The asymmetry is profiles.py's own: can_add_specific_item is on Company Tech
    Soldier and not on Company Commander, while can_change_assignment_others is
    the other way round. Faithfully carried over rather than tidied up.
    """
    tech_item = item_named(db_session, "TA300")

    # Both refusals first. The permitted transfer at the end moves TA300 out of
    # the tech's hands, and the tech can see TA300 only because they hold it --
    # they have no VIEW grant. Run in the other order, their 403 turns into a
    # 404 that is true for an unrelated reason.
    assert create(client, "u_cmdr_a", "VERB1").status_code == 403
    assert transfer(
        client, "u_tech_a", tech_item.id, mock_matrix_db["soldier_b"].id
    ).status_code == 403, "the tech may not move even the item they are holding"

    assert create(client, "u_tech_a", "VERB2").status_code == 200
    assert transfer(
        client, "u_cmdr_a", tech_item.id, mock_matrix_db["soldier_a"].id
    ).status_code == 200


# --- which way authority travels --------------------------------------------

def test_a_grant_above_reaches_down_and_a_grant_below_does_not(
    client, db_session, mock_matrix_db, group_graph
):
    """Positional authority, at the route rather than in the algebra.

    The battalion commander's single grant reaches into both companies. Company
    A's commander reaches neither the battalion nor Company B.

    Co A's commander is given a VIEW grant on the battalion first, on purpose.
    Without it the downward half is answered by the RESOLVER -- they cannot see
    Company B's item, so it is 404 and the gate is never consulted. Widening
    only their sight moves the refusal to where this test wants it, and 403 is
    then the assertion that authority did not widen with it.
    """
    cmdr_a = mock_matrix_db["company_cmdr_a"]
    db_session.add(authz.Grant(
        user_id=cmdr_a.id,
        group_id=group_graph["188/53"].id,
        capability=Capability.VIEW.value,
    ))
    db_session.commit()

    elsewhere = item_named(db_session, "SB200")

    # Up and across FIRST. The battalion transfer below hands SB200 to a Company
    # A soldier, which re-derives the item's group to 188/53/A -- squarely inside
    # Co A's own grant. Asserted afterwards, this refusal becomes a 200 and the
    # test silently stops testing anything.
    assert transfer(
        client, "u_cmdr_a", elsewhere.id, mock_matrix_db["soldier_a"].id
    ).status_code == 403, "Company A's grant reaches neither the battalion nor Company B"

    # Down: the battalion's single grant covers Company B.
    assert transfer(
        client, "u_bat_cmdr", elsewhere.id, mock_matrix_db["soldier_a"].id
    ).status_code == 200


# --- SEC-H3, as a mechanism rather than a symptom ---------------------------

# test_a_profile_name_no_longer_confers_transfer_rights used to live here: it
# hand-set soldier_a's Profile to "Company Tech Soldier" (in the old allowlist)
# with can_change_assignment_others forced True, and asserted transfer still
# refused them -- proving the old MECHANISM was gone, not just the symptom.
# H1-12 drops Profile entirely, so there is no profile left to hand-set; the
# guarantee is now structural. What remains ("holds the item, no grant, is
# refused") is test_seeing_an_item_without_a_transfer_grant_is_403 above,
# unaffected by this soldier ever having carried that profile.


# --- master --------------------------------------------------------------

def test_master_acts_on_grants_and_not_on_their_role(client, db_session, mock_matrix_db):
    """What "pure grant-based" has to mean to be a fact rather than a claim.

    Master transfers, then the same account is stripped and tries again. If the
    role still decided anything on either path, a later call would succeed.

    Stripped TWICE, because the two refusals are different facts and H1-10 is
    what separated them:

        TRANSFER revoked, VIEW kept   -> 403. Sees the fleet, moves nothing.
        every grant revoked           -> 404. Does not see it at all.

    Through H1-8 and H1-9 only the first was reachable, because
    scope_equipment_query short-circuited on is_master and a grantless master
    kept their sight no matter what the grant table said. This test recorded
    that as the state of the cutover and said the second assertion was what
    would change at H1-10. It changed: the bypass is deleted, master's sight is
    the root VIEW grant, and revoking it revokes seeing.

    Keeping both is the point. A 404 alone would no longer distinguish "may not"
    from "cannot see", and that distinction is the entire 404-before-403 rule.
    """
    item = item_named(db_session, "SA100")
    master = mock_matrix_db["master"]
    assert transfer(client, "u_master", item.id, mock_matrix_db["soldier_b"].id).status_code == 200

    # Authority gone, sight kept.
    db_session.query(authz.Grant).filter(
        authz.Grant.user_id == master.id,
        authz.Grant.capability != Capability.VIEW.value,
    ).delete(synchronize_session=False)
    db_session.commit()

    res = transfer(client, "u_master", item.id, mock_matrix_db["soldier_a"].id)
    assert res.status_code == 403, res.text

    # Sight gone too.
    db_session.query(authz.Grant).filter_by(user_id=master.id).delete(
        synchronize_session=False
    )
    db_session.commit()

    res = transfer(client, "u_master", item.id, mock_matrix_db["soldier_a"].id)
    assert res.status_code == 404, res.text


# --- the destination side ---------------------------------------------------
#
# H1-8 gated the SOURCE and recorded the other half as an open question:
# "is placing an item into a group the same verb as removing it from one?"
# H1-10.5 answered yes. These are the tests that make the answer real.


@pytest.mark.parametrize("route", ["transfer", "assign"])
def test_a_commander_cannot_push_an_item_into_a_unit_they_do_not_command(
    client, db_session, mock_matrix_db, route
):
    """Both ends of a move are authorised, not just the end it leaves.

    Co A's commander holds TRANSFER over their own company and nothing else.
    SA100 is theirs to move. Handing it to Company B's soldier would re-derive
    the item's group to 188/53/B -- out of their own sight and into a unit they
    have no authority over -- and until this entry nothing asked.

    Both routes, because both re-derive: transfer_equipment from the new
    HOLDER, assign_owner from the new OWNER. Gating one and not the other
    would leave the hole open under a different verb name.

    The refusal is 403 rather than 404 and that is correct: the caller can see
    the item perfectly well, and the target user is not a resource they are
    being told about. Nothing is disclosed by refusing.
    """
    item = item_named(db_session, "SA100")
    before_group = item.group_id
    before_holder = item.holder_user_id
    target = mock_matrix_db["soldier_b"]

    call = transfer if route == "transfer" else assign
    res = call(client, "u_cmdr_a", item.id, target.id)

    assert res.status_code == 403, res.text
    db_session.refresh(item)
    assert item.group_id == before_group, "the item changed units anyway"
    assert item.holder_user_id == before_holder


@pytest.mark.parametrize("route", ["transfer", "assign"])
def test_the_level_containing_both_units_may_move_between_them(
    client, db_session, mock_matrix_db, group_graph, route
):
    """The other half, and the argument for one verb rather than two.

    The battalion commander holds TRANSFER over 188/53, and desc(188/53)
    contains both companies -- so the SAME grant satisfies the source gate and
    the destination gate, and the cross-company handover goes through. Nothing
    special-cases this; the algebra says 'the level that contains both parties'
    on its own.

    Without this test the entry would read as 'cross-unit transfer is banned',
    which is not what it does. It relocated the authority to the level that
    can see both ends.
    """
    item = item_named(db_session, "SA100")
    target = mock_matrix_db["soldier_b"]

    call = transfer if route == "transfer" else assign
    res = call(client, "u_bat_cmdr", item.id, target.id)

    assert res.status_code == 200, res.text
    db_session.refresh(item)
    assert item.group_id == group_graph["188/53/B"].id
    assert item.holder_user_id == target.id


def test_a_handover_inside_one_unit_asks_nothing_extra(
    client, db_session, mock_matrix_db
):
    """The guard that keeps the ordinary case ordinary.

    Company A's commander hands SA100 to Company A's tech. Source and
    destination are the same group, so the second require() is skipped
    entirely rather than asked and answered.

    This is what fails if the `destination != item.group_id` guard is dropped
    and replaced with an unconditional second gate -- which would still pass
    for this caller, since they hold the verb here. The sharper case is below.
    """
    item = item_named(db_session, "SA100")
    tech = mock_matrix_db["company_tech_a"]

    res = transfer(client, "u_cmdr_a", item.id, tech.id)

    assert res.status_code == 200, res.text
    db_session.refresh(item)
    assert item.holder_user_id == tech.id


def test_a_target_who_belongs_to_no_group_leaves_the_item_where_it_is(
    client, db_session, mock_matrix_db
):
    """The `is not None` guard, which is the sharp one.

    A target who is a member of nothing has no destination group, so the item
    does not move and there is nothing to authorise. Drop that guard and
    require() is handed None -- which it refuses by design -- so handing an
    item to an unassigned soldier would 403 for everyone including the master.

    The stray is built here rather than borrowed, because every fixture account
    is a member of exactly one group; the case cannot occur without one.
    """
    stray = models.User(
        personal_number="u_stray", full_name="Unassigned",
        password_hash=mock_matrix_db["soldier_a"].password_hash,
    )
    db_session.add(stray)
    db_session.commit()

    item = item_named(db_session, "SA100")
    before_group = item.group_id

    res = transfer(client, "u_cmdr_a", item.id, stray.id)

    assert res.status_code == 200, res.text
    db_session.refresh(item)
    assert item.holder_user_id == stray.id
    assert item.group_id == before_group, "a groupless holder stranded the item"


# --- the ordering, stated as the property it protects -----------------------

@pytest.mark.parametrize("route", ["transfer", "assign"])
def test_an_unseen_item_and_a_nonexistent_one_answer_identically(
    client, db_session, mock_matrix_db, route
):
    """The enumeration oracle, closed at both write routes.

    Company A's commander holds TRANSFER, so if the gate ran before the resolver
    they would get 403 for Company B's real item and 404 for an invented id --
    and could sweep the id space for which numbers exist. Identical answers is
    the property; the specific code matters less than that there is only one.
    """
    elsewhere = item_named(db_session, "SB200")
    call = transfer if route == "transfer" else assign
    target = mock_matrix_db["soldier_a"].id

    answers = {
        (res.status_code, res.json().get("detail"))
        for res in (
            call(client, "u_cmdr_a", elsewhere.id, target),
            call(client, "u_cmdr_a", 999999, target),
        )
    }

    assert len(answers) == 1, f"the two cases are distinguishable: {answers}"


def test_a_transfer_to_holder_zero_is_not_silently_a_location_transfer(
    client, db_session, mock_matrix_db
):
    """0 is falsy and is not None, and those two facts disagreed.

    The XOR validation decides which kind of transfer this is with `is None`,
    so `to_holder_id: 0` passed as a person transfer. The branch below then
    tested truthiness, so 0 failed it, fell through to the LOCATION branch, and
    wrote `custom_location = req.to_location` -- which is None.

    Result: 200 OK, holder cleared, location null. The item belonged to nobody
    and sat nowhere, and the only thing still making it visible to anyone was
    its group. Pre-existing, and it lived in the branch H1-10.5 rewrote, so it
    is fixed here rather than inherited.

    404 rather than 400: 0 is a syntactically fine user id that does not exist,
    which is the same answer any other nonexistent id gets.
    """
    item = item_named(db_session, "SA100")
    before_holder = item.holder_user_id

    res = transfer(client, "u_cmdr_a", item.id, 0)

    assert res.status_code == 404, res.text
    db_session.refresh(item)
    assert item.holder_user_id == before_holder, "the item was stranded"
    assert item.custom_location is None


def test_a_deliberate_status_code_raised_inside_the_try_survives(
    client, db_session, mock_matrix_db, monkeypatch
):
    """The `except HTTPException: raise` clause, which nothing else reaches.

    DATA-H6 was one instance of a class: a deliberate status code raised inside
    transfer_equipment's try block, flattened into a 500 by the broad
    `except Exception` with its detail string embedded in the new message.
    Moving the target lookup out fixed the instance. The re-raise clause is
    what stops the next one.

    Nothing inside the try raises HTTPException any more, by design -- which is
    exactly why mutation found the clause deletable with the suite green. So
    this test puts a raise in there on purpose: a future edit that adds a
    genuine 404 or 409 inside the block must keep its code, and this is the
    only place that says so.

    418 rather than a plausible code, so a pass cannot be an accident of the
    route producing that status for some other reason.
    """
    from fastapi import HTTPException

    from backend.routers import equipment as equipment_router

    def explode(*args, **kwargs):
        raise HTTPException(status_code=418, detail="deliberate")

    monkeypatch.setattr(equipment_router.models, "TransactionLog", explode)

    item = item_named(db_session, "SA100")
    res = transfer(client, "u_cmdr_a", item.id, mock_matrix_db["company_tech_a"].id)

    assert res.status_code == 418, res.text
    assert res.json()["detail"] == "deliberate", "the detail was re-wrapped"
