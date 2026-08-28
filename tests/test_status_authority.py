"""H1-9: who may write an equipment's status, and who may close the fault.

Derived for what this entry decides rather than carried over from H1-8. Three
things are new here and each one is asserted from both sides:

  1. Two verbs that must not be interchangeable. REPORT_STATUS and
     RESOLVE_FAULT are held by nearly the same people, and a gate reading the
     wrong one would pass almost every test that could be written. The pair
     that separates them is Company A's commander and Company A's tech, in one
     group, on one item, giving opposite answers.

  2. A possession arm that exists at some routes and deliberately not at
     others. Holding an item lets you report a fault on it and does not let you
     declare it fixed.

  3. 404 before 403, at five routes that previously resolved by raw id -- two
     of them reads, which leaked an asset's entire observation history to
     anyone who could count.

Refusals are asserted BEFORE permitted calls throughout. A permitted call here
mutates the very state the refusals are about -- fix_equipment marks the item
Functional and closes its tickets, create_verification rewrites the status and
stamps last_verified_at -- so the other order leaves an assertion standing on a
row the test itself has just changed.
"""
import pytest

from backend import authz, models
from backend.enums import Capability
from tests.conftest import create_auth_header, revoke

# --- helpers ---------------------------------------------------------------
#
# Thin on purpose: each names one route so a call site reads as the question it
# is asking, and none of them assert, so an unexpected status code is reported
# by the test that cared rather than swallowed here.


def item_named(db, serial):
    return db.query(models.Equipment).filter_by(serial_number=serial).one()


def report(client, who, equipment_id, fault="Cracked Housing", description="found on parade"):
    return client.post(
        "/maintenance/report",
        json={"equipment_id": equipment_id, "fault_name": fault, "description": description},
        headers=create_auth_header(who),
    )


def fix(client, who, equipment_id):
    return client.post(f"/maintenance/fix/{equipment_id}", headers=create_auth_header(who))


def verify_daily(client, who, equipment_id):
    return client.post(f"/equipment/{equipment_id}/verify", headers=create_auth_header(who))


def submit_verification(client, who, equipment_id, status="Malfunctioning"):
    return client.post(
        "/verifications/",
        json={
            "equipment_id": equipment_id,
            "verification_type": "DAILY",
            "reported_status": status,
        },
        headers=create_auth_header(who),
    )


def read_verifications(client, who, equipment_id):
    return client.get(f"/verifications/equipment/{equipment_id}", headers=create_auth_header(who))


def read_history(client, who, equipment_id):
    return client.get(f"/equipment/{equipment_id}/history", headers=create_auth_header(who))


def let_them_see(db, user, item):
    """Grant VIEW over one item's group, and nothing else.

    Needed more often than it looks, because the fixture cannot supply the
    case: there is no seeded account that can SEE an item, does not hold it,
    and lacks the verb to write to it. Every VIEW-holder in the graph also
    carries REPORT_STATUS, so without this the resolver answers 404 for
    everyone a gate would have refused and no 403 is reachable at all --
    which is exactly how two gates survived mutation before it existed.
    """
    db.add(authz.Grant(
        user_id=user.id,
        group_id=item.group_id,
        capability=Capability.VIEW.value,
    ))
    db.commit()


# --- the two verbs are not interchangeable ---------------------------------


def test_a_commander_may_report_a_fault_and_may_not_close_it(
    client, db_session, mock_matrix_db
):
    """The entire reason there are two verbs, in one group and on one item.

    Company A's commander holds REPORT_STATUS over 188/53/A and does not hold
    RESOLVE_FAULT: Company Commander is the only seeded profile carrying
    can_change_maintenance_status that is not a tech, so it is the only profile
    the split separates. SA100 is their soldier's item, well inside their VIEW
    extent, so neither answer here is about visibility.

    A gate reading REPORT_STATUS at fix_equipment -- the natural-looking
    mistake, since one boolean stood behind both routes until this entry --
    answers 200 to the second call and passes everything else in this file.
    """
    item = item_named(db_session, "SA100")

    assert fix(client, "u_cmdr_a", item.id).status_code == 403

    res = report(client, "u_cmdr_a", item.id)
    assert res.status_code == 200

    db_session.refresh(item)
    assert item.status == "Malfunctioning"


def test_the_battalion_tech_closes_what_the_company_commander_reported(
    client, db_session, mock_matrix_db
):
    """The other half: RESOLVE_FAULT without holding the item.

    The battalion's tech commander does not hold SA100 -- soldier_a does -- and
    reaches it through a grant one level up, because authority points DOWN.
    Closing is authority, not possession, which is the asymmetry with the
    soldier below.

    Written against the battalion, which since H1-10 is a choice rather than a
    necessity: company_tech_a can now see this item too and would also answer
    200 -- see test_a_maintenance_verb_now_comes_with_the_sight_to_use_it,
    which asserts exactly that. Keeping the battalion here preserves the
    separate claim that authority points DOWN, reaching an item two levels
    below the granted node.
    """
    item = item_named(db_session, "SA100")
    assert report(client, "u_cmdr_a", item.id).status_code == 200

    res = fix(client, "u_bat_cmdr", item.id)
    assert res.status_code == 200

    db_session.refresh(item)
    assert item.status == "Functional"
    open_tickets = db_session.query(models.MaintenanceLog).filter(
        models.MaintenanceLog.equipment_id == item.id,
        models.MaintenanceLog.status != "Closed",
    ).count()
    assert open_tickets == 0


# --- possession is not authority -------------------------------------------


def test_a_soldier_reports_on_what_they_hold_and_cannot_declare_it_fixed(
    client, db_session, mock_matrix_db
):
    """The possession arm, and the route that deliberately does not have it.

    soldier_a holds no grant of any kind -- "u_soldier gets NOTHING,
    deliberately" -- and holds SA100. Both calls below are about the SAME item
    and the SAME user, so the only thing that can produce different answers is
    which routes consult dependencies.require_status_authority.

    Delete the possession arm and the first call becomes 403: a private cannot
    report a fault on the rifle in their hands. Add the arm to fix_equipment
    and the second becomes 200: a private declares their own broken weapon
    serviceable. Both mutations are killed here and nowhere else.
    """
    item = item_named(db_session, "SA100")

    assert fix(client, "u_soldier_a", item.id).status_code == 403

    assert report(client, "u_soldier_a", item.id).status_code == 200
    db_session.refresh(item)
    assert item.status == "Malfunctioning"

    # Still refused after reporting: the fault being theirs changes nothing.
    assert fix(client, "u_soldier_a", item.id).status_code == 403
    db_session.refresh(item)
    assert item.status == "Malfunctioning"


def test_possession_does_not_reach_past_the_item_held(
    client, db_session, mock_matrix_db
):
    """The arm is per-item, not a standing status role.

    soldier_a holds SA100 and nothing else. TA300 is in their own company, so
    an arm keyed on the holder's group rather than on the item would let them
    write status onto their neighbours' kit. They cannot see it either, so the
    answer is 404 -- the resolver refuses before the arm is ever consulted.
    """
    other = item_named(db_session, "TA300")
    before = other.status

    assert report(client, "u_soldier_a", other.id).status_code == 404
    assert submit_verification(client, "u_soldier_a", other.id).status_code == 404

    db_session.refresh(other)
    assert other.status == before


def test_a_verification_is_refused_to_someone_who_can_see_and_may_not_report(
    client, db_session, mock_matrix_db, group_graph
):
    """create_verification's 403 arm, which nothing else in this suite reaches.

    Found by mutation, not by reading the route: deleting this route's gate
    outright left the whole suite green. The reason is a property of the fixture
    rather than an oversight in the tests -- there is no seeded account that can
    SEE an item, does not hold it, and lacks REPORT_STATUS. Every VIEW-holder in
    the graph also carries the verb, so the resolver answered 404 for everyone
    the gate would have refused, and the gate never had to run.

    So the case is constructed. soldier_a is granted VIEW over their own company
    and nothing else: they can now see TA300, which company_tech_a carries, and
    they hold no maintenance verb anywhere. That is the one combination that
    separates 'cannot see it' from 'may not write to it', and it is what makes
    the status write on this route a gate rather than a formality.
    """
    item = item_named(db_session, "TA300")
    let_them_see(db_session, mock_matrix_db["soldier_a"], item)

    before = db_session.query(models.Verification).count()
    assert read_verifications(client, "u_soldier_a", item.id).status_code == 200, (
        "the premise: they can see it now, so 404 is off the table"
    )

    assert submit_verification(client, "u_soldier_a", item.id).status_code == 403
    assert report(client, "u_soldier_a", item.id).status_code == 403

    assert db_session.query(models.Verification).count() == before
    db_session.refresh(item)
    assert item.status == "Functional"


# --- 404 before 403, per route ---------------------------------------------


@pytest.mark.parametrize(
    "call",
    [report, fix, verify_daily, submit_verification, read_verifications, read_history],
    ids=["report", "fix", "verify_daily", "verification", "read_verifications", "read_history"],
)
def test_every_status_route_answers_404_across_a_unit_boundary(
    client, db_session, mock_matrix_db, call
):
    """SEC-H6, closed at all six sites at once.

    Every one of these resolved equipment by raw id before this entry. The two
    reads returned an asset's full observation history -- who checked it, when,
    what they found, and every status change with the user who made it -- to any
    authenticated account that could count to the id.

    soldier_a is used rather than a commander on purpose: they hold no grant, so
    a 403 anywhere here would be the resolver failing to run rather than a
    narrower verb refusing. The answer must be identical for all six.
    """
    item_b = item_named(db_session, "SB200")
    assert call(client, "u_soldier_a", item_b.id).status_code == 404


@pytest.mark.parametrize(
    "serial,expected", [("SA100", 403), ("SB200", 404)], ids=["sees_it", "cannot_see_it"]
)
def test_the_resolver_answers_before_the_verb_does(
    client, db_session, mock_matrix_db, serial, expected
):
    """One account, two items, two different refusals -- and the order is why.

    Company A's commander is refused both times and never learns the same thing
    twice. That is the whole 404-before-403 discipline in one account:

        SA100  in their company, and their verbs do not include RESOLVE_FAULT
               -> 403, and they already knew the item existed
        SB200  one company over, outside their VIEW extent
               -> 404, indistinguishable from an id never issued

    fix_equipment asked permission BEFORE the lookup until this entry, which is
    why both answers used to be the same: a caller who passed the profile check
    was told, by the 404, whether any id anywhere in the force existed. Put the
    gate back above the resolver and this pair collapses into one answer.
    """
    item = item_named(db_session, serial)
    assert fix(client, "u_cmdr_a", item.id).status_code == expected


# --- a refused request writes nothing --------------------------------------


def test_a_refused_report_leaves_no_fault_type_behind(
    client, db_session, mock_matrix_db
):
    """The gate sits above the find-or-create block, which commits.

    report_fault mints a global FaultType row for any name it has not seen. Gate
    the route after that block -- the natural place, right before the log is
    written -- and a refused request still leaves an attacker-chosen row in the
    shared vocabulary, permanently, visible to every unit. Same ordering, and
    the same reason, as create_equipment's catalog block.

    BOTH refusals are exercised, and only the second one actually tests the
    claim. Mutation found that: moving the gate below the commit left the suite
    green while this test refused only across a unit boundary, because that
    refusal comes from the RESOLVER, which still runs first in the mutant and
    returns before any vocabulary is minted. The gate's position is only
    observable to a caller who gets past the resolver and is then refused --
    which needs an account that can SEE an item it may not write to, and the
    fixture has none until one is constructed.
    """
    before = db_session.query(models.FaultType).count()

    # Refused by the resolver: 404, and nothing is reached.
    item_b = item_named(db_session, "SB200")
    assert report(client, "u_soldier_a", item_b.id, fault="ARBITRARY-XYZ").status_code == 404

    # Refused by the GATE: 403, one line above the block that commits.
    seen = item_named(db_session, "TA300")
    let_them_see(db_session, mock_matrix_db["soldier_a"], seen)
    assert report(client, "u_soldier_a", seen.id, fault="ARBITRARY-ABC").status_code == 403

    assert db_session.query(models.FaultType).count() == before
    for name in ("ARBITRARY-XYZ", "ARBITRARY-ABC"):
        assert db_session.query(models.FaultType).filter_by(name=name).first() is None, name


def test_a_refused_write_leaves_no_row_in_any_table(
    client, db_session, mock_matrix_db
):
    """Nothing partial survives a refusal, on any of the four write paths.

    Each of these routes writes to a different table, and each writes it AFTER
    the gate. Counting all four together is what catches a gate that was moved
    below one write while staying above the others.
    """
    item_b = item_named(db_session, "SB200")
    counts = {
        models.MaintenanceLog: db_session.query(models.MaintenanceLog).count(),
        models.Verification: db_session.query(models.Verification).count(),
        models.EquipmentStatusHistory: db_session.query(models.EquipmentStatusHistory).count(),
        models.TransactionLog: db_session.query(models.TransactionLog).count(),
    }
    before_status = item_b.status

    assert report(client, "u_soldier_a", item_b.id).status_code == 404
    assert fix(client, "u_soldier_a", item_b.id).status_code == 404
    assert verify_daily(client, "u_soldier_a", item_b.id).status_code == 404
    assert submit_verification(client, "u_soldier_a", item_b.id).status_code == 404

    for model, before in counts.items():
        assert db_session.query(model).count() == before, model.__name__
    db_session.refresh(item_b)
    assert item_b.status == before_status


# --- is_pending: the last profile read in maintenance.py --------------------


def test_a_novel_fault_type_is_pending_only_for_a_possession_only_reporter(
    client, db_session, mock_matrix_db
):
    """is_manager stopped being a profile column and became a grant question.

    can_change_maintenance_status decided this before; REPORT_STATUS is where
    that column went, so every seeded profile gets the answer it got before. The
    case that is genuinely new is the reporter who passed the gate on possession
    alone and holds no grant at all: their novel vocabulary now waits for
    approval, which is what a pending flag is for.

    Switch the query to RESOLVE_FAULT and the commander's fault type joins the
    queue too -- a queue API-H6 records as having no way to drain it.
    """
    item = item_named(db_session, "SA100")

    assert report(client, "u_soldier_a", item.id, fault="HOLDER-NOVEL").status_code == 200
    holder_fault = db_session.query(models.FaultType).filter_by(name="HOLDER-NOVEL").one()
    assert holder_fault.is_pending is True

    assert report(client, "u_cmdr_a", item.id, fault="COMMANDER-NOVEL").status_code == 200
    granted_fault = db_session.query(models.FaultType).filter_by(name="COMMANDER-NOVEL").one()
    assert granted_fault.is_pending is False


def test_a_maintenance_verb_now_comes_with_the_sight_to_use_it(
    client, db_session, mock_matrix_db
):
    """The positive form of what this test used to assert, after H1-10.

    Written in H1-9 as test_a_verb_held_without_sight_can_never_be_exercised,
    asserting 404: Company A's tech held RESOLVE_FAULT over SA100's group and
    could not see the item, because the resolver asks VIEW first and this
    fixture's Company Tech Soldier row set can_view_company_realtime false
    where profiles.py sets it true. It said so, and said H1-10 was where it
    would fail. It did, and this is the replacement.

    The rule is now: you see what you may maintain. Both halves are asserted,
    because the grant alone is not the interesting part -- the same account
    reaching an item it does NOT hold, in a company it does not command, is
    what a VIEW grant bought. Take that grant away and this returns to 404,
    which is the state H1-9 described.
    """
    item = item_named(db_session, "SA100")
    tech = mock_matrix_db["company_tech_a"]

    assert item.holder_user_id != tech.id, (
        "the premise: this is a company-mates item, not the techs own"
    )
    holders = db_session.query(authz.Grant).filter_by(
        capability=Capability.RESOLVE_FAULT.value,
        group_id=item.group_id,
    ).all()
    assert {g.user_id for g in holders} == {tech.id}, (
        "the premise: Company A's tech is granted RESOLVE_FAULT over this group"
    )

    assert report(client, "u_cmdr_a", item.id).status_code == 200
    assert fix(client, "u_tech_a", item.id).status_code == 200

    db_session.refresh(item)
    assert item.status == "Functional"


# --- the arms that were deleted --------------------------------------------
#
# test_a_profile_boolean_no_longer_grants_maintenance_authority used to live
# here: it hand-set soldier_a's can_change_maintenance_status Profile boolean
# to True and asserted the status routes still refused them, proving the old
# INPUT had stopped being consulted, not merely that the old symptom (an
# xfail) was gone. H1-12 drops Profile entirely, so there is no boolean left
# to hand-set -- the guarantee is now structural. What remains ("can see,
# holds no grant, is refused") is
# test_a_verification_is_refused_to_someone_who_can_see_and_may_not_report
# above, unaffected by this soldier ever having carried that flag.


def test_master_writes_status_on_grants_and_not_on_their_role(
    client, db_session, mock_matrix_db
):
    """The is_master arm is gone from the helper, and this is what proves it.

    Master's authority is the every-capability grant on the root, issued by the
    seed, this fixture and bootstrap_admin alike. Strip the two maintenance
    verbs and master must be refused -- and refused with 403, not 404, because
    scope_equipment_query still short-circuits VISIBILITY on the role until
    H1-10. Seeing the whole force and being able to write to none of it is the
    exact state of the cutover, and this test is what changes when that last
    bypass goes.
    """
    item = item_named(db_session, "SA100")
    assert report(client, "u_master", item.id).status_code == 200

    revoke(db_session, mock_matrix_db["master"], Capability.REPORT_STATUS, Capability.RESOLVE_FAULT)

    assert report(client, "u_master", item.id).status_code == 403
    assert fix(client, "u_master", item.id).status_code == 403
    # Still sees it. Visibility and authority have separated.
    assert read_history(client, "u_master", item.id).status_code == 200


# --- the reads --------------------------------------------------------------


def test_history_is_readable_by_those_who_can_see_the_item(
    client, db_session, mock_matrix_db
):
    """The reads take the resolver and no verb, and that is deliberate.

    A read gated by a capability would demand authority to see the history of an
    item the caller can already list, hold and verify. The resolver IS the VIEW
    gate here, so the boundary is visibility and nothing else: the commander who
    can see SA100 reads its history, and Company B's tech -- who holds both
    maintenance verbs, one company over -- gets 404.
    """
    item = item_named(db_session, "SA100")
    assert submit_verification(client, "u_soldier_a", item.id).status_code == 200

    for who in ("u_soldier_a", "u_cmdr_a", "u_bat_cmdr"):
        res = read_verifications(client, who, item.id)
        assert res.status_code == 200, who
        assert len(res.json()) == 1

        assert read_history(client, who, item.id).status_code == 200

    assert read_verifications(client, "u_tech_b", item.id).status_code == 404
    assert read_history(client, "u_tech_b", item.id).status_code == 404


def test_a_verification_writes_status_history_and_a_refusal_does_not(
    client, db_session, mock_matrix_db
):
    """create_verification's gate, from both sides, on the row it produces.

    Company B's commander holds REPORT_STATUS over Company B, so they may write
    status onto SB200 without holding it -- authority, not possession.
    soldier_a may not, and cannot see it, so the history table gains nothing
    from them.
    """
    item_b = item_named(db_session, "SB200")
    before = db_session.query(models.EquipmentStatusHistory).count()

    assert submit_verification(client, "u_soldier_a", item_b.id).status_code == 404
    assert db_session.query(models.EquipmentStatusHistory).count() == before

    assert submit_verification(client, "u_cmdr_b", item_b.id).status_code == 200
    db_session.refresh(item_b)
    assert item_b.status == "Malfunctioning"

    history = db_session.query(models.EquipmentStatusHistory).filter_by(
        equipment_id=item_b.id
    ).one()
    assert (history.old_status, history.new_status) == ("Functional", "Malfunctioning")


def test_the_daily_verify_route_keeps_its_holder_rule(
    client, db_session, mock_matrix_db
):
    """verify_equipment_daily gained the resolver and NOT the possession arm.

    It records "I am carrying this", so possession-OR-grant would be strictly
    wider than the rule it has: a commander could file a presence confirmation
    for kit they are not holding. The commander below sees SA100 and holds
    REPORT_STATUS over it, and is still refused -- which is the assertion that
    fails the moment someone swaps this check for the shared helper on the
    grounds that the two look alike.
    """
    item = item_named(db_session, "SA100")

    assert verify_daily(client, "u_cmdr_a", item.id).status_code == 403
    assert verify_daily(client, "u_tech_b", item.id).status_code == 404
    assert verify_daily(client, "u_soldier_a", item.id).status_code == 200
