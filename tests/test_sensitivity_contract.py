"""DATA-H3: equipment sensitivity -- reported (-1), writable (-2), enforced (-3).

Three tickets, three sections, and the split matters when reading a failure.
-1 made the RESPONSE track the column. -2 gave the field a WRITE PATH and an
authority (Capability.SET_SENSITIVITY). -3 made it a VISIBILITY CONTROL: a
CLASSIFIED item is hidden from a caller lacking Capability.VIEW_CLASSIFIED over
its group, filtered once in dependencies.scope_equipment_query.

With -3 the whole of the audit entry is answered. Its Fix line read "populate
it from the record, constrain it to an enumerated type, and either enforce it
in scoping or delete the concept"; the enforce branch was taken, so the
complaint this file opened against -- "a security control that exists only as a
column" -- no longer holds.

Two limits on that guarantee, both deliberate and both pinned below:

  * POSSESSION SURVIVES. The holder arm is outside the classification clause,
    so classification hides an item from everyone EXCEPT whoever carries it.
    test_sensitivity_does_not_widen_or_narrow_scope is that pin -- it was -2's
    tripwire and needed no change to its assertions, only its docstring.
  * AGGREGATES LEAK. analytics.unit_readiness counts through the same scope, so
    an uncleared caller's total drops by one. Accepted, and pinned so it cannot
    change silently.

=== DATA-H3-1: responses report the sensitivity the record holds ===

The defect: `EquipmentResponse.sensitivity` was declared `str = "UNCLASSIFIED"`
and passed by none of the three construction sites (equipment.py's accessible
and create routes, users.py's my-equipment route). Pydantic supplies the
default when a field is not passed, so the wire value was a CONSTANT -- the
column was never read, and an item classified by hand in the database was
reported unclassified to every caller.

Worse than the usual drift, because there is no writer either: `sensitivity` is
absent from EquipmentCreate and from backend/seed_data.py, and no route updates
it. In production the column has only ever held its own Python-side default.
That is why the tests below have to reach past the API to set up: with no write
path, a CLASSIFIED row cannot be produced through HTTP at all.

One caveat on that, so nobody reads more into the green than is there: the test
fixtures DO write the value explicitly (conftest.py:472,479,486). So
test_an_unclassified_record_still_reports_unclassified is passing on a seeded
literal, not on the model default, and would not notice that default breaking.
The default is pinned instead by
test_created_equipment_reports_its_stored_sensitivity, which reads back the row
POST /equipment/ actually wrote.

WHAT EACH TEST ACTUALLY DETECTS
-------------------------------
Measured by mutation, not asserted, and the numbers below were re-measured
after a code review caught a stale count -- they are what the runs printed:

  * schema constant restored -> 6 of these 10 red, including both
      and all three kwargs      parametrizations of
      dropped                   test_sensitivity_is_read_from_the_record.
                                Keep enums.py when reproducing: this module
                                imports Sensitivity, so reverting that file
                                too yields a collection error, not failures.
  * Optional[Sensitivity]    -> only test_an_out_of_vocabulary_value_... red,
      weakened to               so the enum clause has its own dedicated pin
      Optional[str]             that does not overlap the population clause
  * kwarg dropped at the     -> only the two [/users/me/equipment] cases red;
      users.py site only        the /equipment/accessible ones stay green
  * kwarg dropped at the     -> the two [/equipment/accessible] cases and the
      accessible site only      search-filter test red; users.py stays green

That last one is the point of parametrizing over LIST_ENDPOINTS: the two routes
build EquipmentResponse by hand from separate copies of the same code, so a
regression at one site must not be able to hide behind the other.

Those -1 tests still reach past the API to set up, and that is now a
deliberate choice rather than a necessity: they pin that the RESPONSE follows
the COLUMN, so writing the column directly is the shorter path to the thing
being asserted. Section 4 exercises the API write path instead.

=== DATA-H3-2: the write path and its authority ===

Section 4 below. CLASSIFIED is now reachable through HTTP -- by PATCH
/equipment/{id}/sensitivity, and by POST /equipment/ naming it -- both gated on
Capability.SET_SENSITIVITY over the item's group.

The two 403 tests are the ones worth protecting. Neither company_cmdr_a (who
commands the item's company) nor soldier_a (who HOLDS the item) carries the
verb in conftest's grant table, and that absence is the fixture stating a
policy: classifying is a command decision above the company, and possession
never confers it. If someone adds SET_SENSITIVITY to either row, those tests go
green for the wrong reason -- check the grant table before believing a pass.

=== DATA-H3-3: the value restricts who may see the row ===

Section 5 below. One filter, in dependencies.scope_equipment_query, qualifying
its VIEW arm: a CLASSIFIED row is visible through that arm only to a caller
whose VIEW_CLASSIFIED extent covers the item's group. Because that function
also backs get_scoped_equipment_or_404 and scope_equipment_derived_query, the
rule reaches every listing, every by-id 404, and the maintenance and
transaction-log listings without any of those routes naming it.

The two tests worth protecting here are the mirror of -2's pair. company_cmdr_a
holds VIEW over the item's company and NOT VIEW_CLASSIFIED, so a classified
item in their own company disappears from their listing -- that is the core
assertion, and it is only meaningful while that absence holds in conftest's
grant table. soldier_a holds no grant at all but CARRIES the item, and still
sees it. Adding VIEW_CLASSIFIED to either row turns these green for the wrong
reason.

Together the three sections now pin a reporting contract, a write contract and
a confidentiality guarantee -- with the two limits named at the top.
"""
import pytest
from pydantic import ValidationError
from sqlalchemy import text

from backend import authz, models
from backend.enums import Capability, Sensitivity

# The two routes that serialize a LIST of equipment. Both build
# EquipmentResponse by hand from a row, so both are independent copies of the
# same bug and are pinned separately -- see this module's note on DATA-H9.
LIST_ENDPOINTS = ["/equipment/accessible", "/users/me/equipment"]


def _item(db_session, serial):
    return (
        db_session.query(models.Equipment)
        .filter(models.Equipment.serial_number == serial)
        .one()
    )


def _get(client, url, token):
    response = client.get(url, headers=token)
    assert response.status_code == 200, response.text
    return response.json()


def _by_serial(payload, serial):
    match = [row for row in payload if row["serial_number"] == serial]
    assert match, f"{serial} missing from response: {[r['serial_number'] for r in payload]}"
    return match[0]


# --- 1. The bug, stated smallest ---------------------------------------------


@pytest.mark.parametrize("url", LIST_ENDPOINTS)
def test_sensitivity_is_read_from_the_record(
    client, mock_matrix_db, db_session, token_soldier, url
):
    """THE regression test -- the only one here that fails on the old code.

    Deliberately sets CLASSIFIED through the ORM rather than the API, because
    no API path can set it: that absence is half of what DATA-H3 reports. The
    assertion is that the wire value FOLLOWS the column. Under the old schema
    this returned "UNCLASSIFIED" for a row that plainly said otherwise.

    Uses the soldier's own item (SA100) so it holds on both endpoints: the
    my-equipment route lists what the caller HOLDS, not what they may see.
    """
    item = _item(db_session, "SA100")
    item.sensitivity = Sensitivity.CLASSIFIED.value
    db_session.commit()

    row = _by_serial(_get(client, url, token_soldier), "SA100")

    assert row["sensitivity"] == "CLASSIFIED", (
        f"{url} reported {row['sensitivity']!r} for a record whose column says "
        "CLASSIFIED -- the field is a constant again, not a read"
    )


@pytest.mark.parametrize("url", LIST_ENDPOINTS)
def test_an_unclassified_record_still_reports_unclassified(
    client, mock_matrix_db, db_session, token_soldier, url
):
    """The other half of the same claim, and not redundant with it.

    A schema that hardcoded CLASSIFIED would pass the test above. Pinning both
    values is what makes the pair say "tracks the column" rather than "emits
    some particular string".
    """
    row = _by_serial(_get(client, url, token_soldier), "SA100")

    assert row["sensitivity"] == "UNCLASSIFIED"


def test_the_search_filter_branch_also_carries_sensitivity(
    client, mock_matrix_db, db_session, token_soldier
):
    """/equipment/accessible has a second code path, and it is the fragile one.

    Passing query_str joins CatalogItem onto the query before the same response
    loop runs. The join is the kind of thing that quietly changes what a row
    yields -- a future switch to explicit column selection, or to a .with_entities
    projection built for the filtered case, would drop sensitivity on this branch
    while the unfiltered tests above stayed green.

    Verified reachable rather than assumed: the fixture's catalog is named
    "Standard Radio" (conftest.py:452), so the substring below actually matches.
    A filter that matched nothing would make this test vacuously pass, which is
    why it asserts on a non-empty result before asserting on the value.
    """
    item = _item(db_session, "SA100")
    item.sensitivity = Sensitivity.CLASSIFIED.value
    db_session.commit()

    payload = _get(client, "/equipment/accessible?query_str=Radio", token_soldier)

    assert payload, "the filter matched nothing -- this test would pass vacuously"
    assert _by_serial(payload, "SA100")["sensitivity"] == "CLASSIFIED"


def test_created_equipment_reports_its_stored_sensitivity(
    client, mock_matrix_db, db_session, token_master
):
    """The third construction site (POST /equipment/), pinned honestly.

    This test CANNOT go red on the old code and the docstring says so rather
    than implying a red-green cycle it never had. With no write path, a freshly
    created row's only reachable value is the model default UNCLASSIFIED --
    the same string the old constant emitted, so the two are indistinguishable
    from outside. What it does pin: the route passes the field at all, the
    value agrees with what was actually persisted, and the model default is a
    real enum member rather than a stray literal.

    The site's protection against re-hardcoding comes from sharing one schema
    with the two list routes, which test_sensitivity_is_read_from_the_record
    does cover.
    """
    response = client.post(
        "/equipment/", json={"catalog_name": "M4", "serial_number": "NEW-H3-1"},
        headers=token_master,
    )
    assert response.status_code == 200, response.text
    body = response.json()

    stored = _item(db_session, "NEW-H3-1")
    assert body["sensitivity"] == stored.sensitivity, (
        "the created item's response disagrees with the row that was written"
    )
    assert stored.sensitivity == Sensitivity.UNCLASSIFIED.value
    assert body["sensitivity"] in {s.value for s in Sensitivity}


def test_a_created_item_reports_classified_once_its_column_says_so(
    client, mock_matrix_db, db_session, token_master
):
    """Closes the gap the test above cannot: proves the create path READS.

    test_created_equipment_reports_its_stored_sensitivity can only ever see
    UNCLASSIFIED, so it cannot distinguish "reads the column" from "emits a
    constant that happens to match". This one creates through the API, then
    classifies the row and re-reads it, so the value on the wire is one no
    constant could have produced.

    The re-read goes through /equipment/accessible rather than POST, because
    POST is not idempotent and there is no GET /equipment/{id} -- so this
    pins the created ROW's reporting, and the create site's own kwarg stays
    pinned by the shared schema plus the stored-value assertion above. Named
    rather than hidden: the create response itself remains the one site with
    no test that can go red on the old code.
    """
    created = client.post(
        "/equipment/", json={"catalog_name": "M4", "serial_number": "NEW-H3-1-B"},
        headers=token_master,
    )
    assert created.status_code == 200, created.text
    assert created.json()["sensitivity"] == "UNCLASSIFIED"

    row = _item(db_session, "NEW-H3-1-B")
    row.sensitivity = Sensitivity.CLASSIFIED.value
    db_session.commit()

    payload = _get(client, "/equipment/accessible", token_master)
    assert _by_serial(payload, "NEW-H3-1-B")["sensitivity"] == "CLASSIFIED"


# --- 2. The reason the field is Optional --------------------------------------


def test_a_null_sensitivity_does_not_take_down_the_whole_list(
    client, mock_matrix_db, db_session, token_master
):
    """The DATA-M12 guard, and the justification for Optional over required.

    A raw UPDATE reaches a state the Python-side `default=` cannot be talked
    out of producing. Not contrived: any pre-existing row, bulk import, or
    non-ORM insert lands here, and the column permits it
    (4acc9d5f6339:108, nullable=True).

    The assertion that matters is the SECOND item. A required `sensitivity`
    fails validation on the null row, and because FastAPI validates the
    response model over the whole list, the caller loses every OTHER item too
    -- one bad row blanks the entire equipment page rather than one line of it.
    """
    nulled = _item(db_session, "SA100")
    db_session.execute(
        text("UPDATE equipment SET sensitivity = NULL WHERE id = :id"),
        {"id": nulled.id},
    )
    db_session.commit()

    payload = _get(client, "/equipment/accessible", token_master)

    assert _by_serial(payload, "SA100")["sensitivity"] is None
    assert _by_serial(payload, "SB200")["sensitivity"] == "UNCLASSIFIED", (
        "a single NULL sensitivity removed an unrelated item from the response "
        "-- the field has been tightened to required, see this module's docstring"
    )


def test_an_out_of_vocabulary_value_fails_loudly_rather_than_reading_unclassified(
    client, mock_matrix_db, db_session, token_master
):
    """Documents the trade DATA-H3-1 makes on purpose.

    The column is unconstrained free text (DATA-H12), so a hand-written value
    outside the enum is reachable. Typing the response as the enum means such a
    row now fails validation instead of being silently replaced by the old
    "UNCLASSIFIED" constant.

    That is a real cost -- one junk row blanks the list, DATA-M12's shape --
    accepted because the alternative is the exact falsehood this ticket exists
    to remove: reporting a record of UNKNOWN classification as unclassified.
    Failing is the fail-CLOSED direction for a classification field.

    This test asserts the behaviour rather than endorsing it permanently.
    DATA-H12 constrains the column and makes the state unreachable; when it
    lands, this test should be revisited, not silently deleted.

    Asserts the ValidationError rather than a 500 because that is what actually
    happens: response-model validation runs AFTER the route returns, so the
    error escapes the request cycle rather than being caught by the router's
    own exception handling. Starlette's TestClient re-raises it by default
    (raise_server_exceptions), so it surfaces here as an exception, not a
    status code. In deployment the same failure is a 500 -- the point being
    pinned is that the junk value is never quietly reported as UNCLASSIFIED.
    """
    item = _item(db_session, "SA100")
    db_session.execute(
        text("UPDATE equipment SET sensitivity = 'BANANA' WHERE id = :id"),
        {"id": item.id},
    )
    db_session.commit()

    with pytest.raises(ValidationError) as excinfo:
        client.get("/equipment/accessible", headers=token_master)

    assert "sensitivity" in str(excinfo.value)
    assert "BANANA" in str(excinfo.value)


# --- 3. Scope is unchanged ----------------------------------------------------


def test_sensitivity_does_not_widen_or_narrow_scope(
    client, mock_matrix_db, db_session, token_soldier
):
    """Classifying an item does not change what its HOLDER sees.

    This was DATA-H3-2's tripwire, written to go red if enforcement arrived
    without its ticket. DATA-H3-3 is that ticket, and the notable thing is that
    this test still passes UNCHANGED in its assertions -- only the docstring
    moved, because what it pins turned out to be exactly the guarantee H3-3
    chose to keep.

    token_soldier HOLDS SA100 and carries no grant at all. The holder arm of
    scope_equipment_query sits outside the new classification clause, so
    possession survives classification: you can always see what you are
    carrying. Section 5 covers the other side -- company_cmdr_a, who sees the
    same item by VIEW rather than by possession, loses it.

    The pair of assertions is still worth keeping together. If a future edit
    folds the holder arm INSIDE the classification clause, the first assertion
    fails and takes the fault-reporting path down with it (a holder who 404s on
    their own item can never reach require_status_authority). The second
    assertion guards the opposite error -- that the clause was written so
    loosely it started admitting a neighbour's inventory.
    """
    a_item = _item(db_session, "SA100")
    a_item.sensitivity = Sensitivity.CLASSIFIED.value
    db_session.commit()

    payload = _get(client, "/equipment/accessible", token_soldier)
    serials = {row["serial_number"] for row in payload}

    assert "SA100" in serials, (
        "a CLASSIFIED item vanished from its own holder's listing -- the "
        "holder arm has been folded inside DATA-H3-3's classification clause, "
        "which also severs this soldier's ability to report a fault on it"
    )
    assert "SB200" not in serials, "a Company B item leaked into Company A's listing"


# --- 4. The write path (DATA-H3-2) -------------------------------------------
#
# Actors, and why each was chosen. All four come from conftest's grant table:
#
#   token_bat_cmdr      holds SET_SENSITIVITY over 188/53 -> the happy path
#   token_company_cmdr  commands 188/53/A, holds TRANSFER, NOT SET_SENSITIVITY
#                       -> may MOVE the item and may not CLASSIFY it
#   token_soldier       HOLDS SA100, holds no grant at all
#                       -> possession does not confer classification
#   token_company_tech  holds CREATE_EQUIPMENT, not SET_SENSITIVITY
#                       -> may create plainly, may not create classified
#
# The verb is absent from the last three by design, not by omission. Adding it
# to any of them turns these tests green for the wrong reason.


def _patch_sensitivity(client, equipment_id, value, token):
    return client.patch(
        f"/equipment/{equipment_id}/sensitivity",
        json={"sensitivity": value},
        headers=token,
    )


def test_the_verb_holder_can_classify_and_the_change_is_read_back(
    client, mock_matrix_db, db_session, token_bat_cmdr, token_soldier
):
    """The happy path, asserted as a ROUND TRIP rather than as a 200.

    A 200 proves the route ran. What matters is that the column changed and
    that the change is what subsequent readers see, so this re-reads through a
    different route (and a different caller) than the one that wrote.
    """
    item = _item(db_session, "SA100")
    assert item.sensitivity == Sensitivity.UNCLASSIFIED.value, "fixture moved"

    response = _patch_sensitivity(
        client, item.id, Sensitivity.CLASSIFIED.value, token_bat_cmdr
    )
    assert response.status_code == 200, response.text
    assert response.json()["sensitivity"] == "CLASSIFIED"

    db_session.expire_all()
    assert _item(db_session, "SA100").sensitivity == Sensitivity.CLASSIFIED.value

    row = _by_serial(_get(client, "/equipment/accessible", token_soldier), "SA100")
    assert row["sensitivity"] == "CLASSIFIED", (
        "the classification did not survive to another caller's listing"
    )


def test_declassifying_is_an_explicit_value_and_also_works(
    client, mock_matrix_db, db_session, token_bat_cmdr
):
    """The reverse direction. A one-way control would be a trap of its own.

    Also pins that UNCLASSIFIED travels through the same gate: declassifying
    is a classification decision, not an absence of one, which is why
    SetSensitivityRequest.sensitivity is required rather than Optional.
    """
    item = _item(db_session, "SA100")
    item.sensitivity = Sensitivity.CLASSIFIED.value
    db_session.commit()

    response = _patch_sensitivity(
        client, item.id, Sensitivity.UNCLASSIFIED.value, token_bat_cmdr
    )
    assert response.status_code == 200, response.text
    assert response.json()["sensitivity"] == "UNCLASSIFIED"

    db_session.expire_all()
    assert _item(db_session, "SA100").sensitivity == Sensitivity.UNCLASSIFIED.value


def test_holding_the_item_does_not_confer_the_right_to_classify_it(
    client, mock_matrix_db, db_session, token_soldier
):
    """THE test of this ticket. Possession is not classification authority.

    soldier_a HOLDS SA100 and holds no grant whatsoever. They can see it
    (scope_equipment_query ORs the holder in) and they can report a fault on
    it (require_status_authority's possession arm), so 404 is not the answer
    here -- 403 is, and the difference is the whole point: the route resolved
    the item fine and refused the ACT.

    The tempting implementation is require_status_authority, which is
    possession-OR-grant and would make this return 200. That helper is the
    wrong tool for this route, and this test is what says so.
    """
    item = _item(db_session, "SA100")
    assert item.holder_user_id is not None

    response = _patch_sensitivity(
        client, item.id, Sensitivity.CLASSIFIED.value, token_soldier
    )

    assert response.status_code == 403, (
        f"the item's HOLDER classified it (got {response.status_code}) -- "
        "possession has become classification authority"
    )
    db_session.expire_all()
    assert _item(db_session, "SA100").sensitivity == Sensitivity.UNCLASSIFIED.value, (
        "refused with 403 and wrote anyway"
    )


def test_commanding_the_item_is_not_enough_without_the_verb(
    client, mock_matrix_db, db_session, token_company_cmdr
):
    """403, not 404: company_cmdr_a can SEE the item and may not classify it.

    This is the pair to the test above from the other direction -- authority
    over the group, but not THIS verb over it. company_cmdr_a holds TRANSFER
    on 188/53/A, so they may move this very item; the two answers differing is
    the entire reason SET_SENSITIVITY is its own verb rather than a reuse of
    TRANSFER.
    """
    item = _item(db_session, "SA100")

    response = _patch_sensitivity(
        client, item.id, Sensitivity.CLASSIFIED.value, token_company_cmdr
    )

    assert response.status_code == 403, (
        f"got {response.status_code}: a commander who may only MOVE the item "
        "classified it -- SET_SENSITIVITY has collapsed into TRANSFER"
    )


def test_an_unseeable_item_answers_404_and_not_403(
    client, mock_matrix_db, db_session, token_soldier
):
    """Resolve-then-decide: the route must not be an enumeration oracle.

    soldier_a cannot see Company B's item at all. The answer must be 404 --
    the same answer they would get for an id that does not exist -- so the
    status code cannot be used to discover which ids have been issued across
    the force. A 403 here would confirm the row exists.

    Goes red if anyone replaces get_scoped_equipment_or_404 with a raw id
    lookup, which is the shape SEC-H6 found at three other sites.
    """
    b_item = _item(db_session, "SB200")

    response = _patch_sensitivity(
        client, b_item.id, Sensitivity.CLASSIFIED.value, token_soldier
    )

    assert response.status_code == 404, (
        f"got {response.status_code} for an item the caller cannot see -- "
        "the route confirms existence and is an id oracle"
    )


def test_creating_a_classified_item_needs_the_verb_and_writes_nothing_without_it(
    client, mock_matrix_db, db_session, token_company_tech
):
    """The create-path gate, and that a refusal leaves NO row behind.

    company_tech_a holds CREATE_EQUIPMENT over 188/53/A and not
    SET_SENSITIVITY, so this request is exactly the one the conditional gate
    exists to refuse.

    The row-count assertion is not padding. create_equipment COMMITS a
    CatalogItem before building the equipment, so a gate placed after that
    block would answer 403 and still leave a permanent row behind under an
    attacker-chosen catalog name -- which is why the check sits with the
    CREATE_EQUIPMENT one, above the commit.
    """
    before = db_session.query(models.Equipment).count()

    response = client.post(
        "/equipment/",
        json={
            "catalog_name": "CLASSIFIED-PROBE",
            "serial_number": "NEW-H3-2-DENIED",
            "sensitivity": Sensitivity.CLASSIFIED.value,
        },
        headers=token_company_tech,
    )

    assert response.status_code == 403, (
        f"got {response.status_code}: CREATE_EQUIPMENT alone created a "
        "CLASSIFIED item -- the conditional gate is not being asked"
    )

    db_session.expire_all()
    assert db_session.query(models.Equipment).count() == before, "a refused creation wrote a row"
    assert (
        db_session.query(models.CatalogItem)
        .filter(models.CatalogItem.name == "CLASSIFIED-PROBE")
        .first()
        is None
    ), "a refused creation left a CatalogItem behind under a caller-chosen name"


def test_ordinary_creation_is_untaxed_by_the_new_verb(
    client, mock_matrix_db, db_session, token_company_tech
):
    """The other half of the conditional gate, and the reason it is conditional.

    Same caller as the test above, same missing verb, no sensitivity named:
    this must succeed. A gate asked unconditionally would break creation for
    every CREATE_EQUIPMENT holder who lacks SET_SENSITIVITY -- both company
    techs in this fixture -- which is what makes EquipmentCreate.sensitivity
    default to None rather than to UNCLASSIFIED.
    """
    response = client.post(
        "/equipment/",
        json={"catalog_name": "M4", "serial_number": "NEW-H3-2-PLAIN"},
        headers=token_company_tech,
    )

    assert response.status_code == 200, (
        f"got {response.status_code}: ordinary creation now demands "
        "SET_SENSITIVITY -- the gate is unconditional"
    )
    assert response.json()["sensitivity"] == "UNCLASSIFIED"


def test_created_equipment_without_a_sensitivity_is_unclassified_not_null(
    client, mock_matrix_db, db_session, token_master
):
    """The column default must fire -- NULL and UNCLASSIFIED are not the same.

    Asserted against the COLUMN, not the response, because both values are
    reported faithfully since DATA-H3-1: NULL serializes as "no answer" and
    UNCLASSIFIED as "not classified", so only the stored row tells them apart.

    HONEST ABOUT WHAT THIS PINS. The route passes None straight through when
    the client names no sensitivity, and SQLAlchemy applies a Column default
    whenever the value is None at flush time -- so no reasonable spelling of
    that line writes NULL, and mutating it does not turn this red. A first
    draft believed otherwise and built a conditional kwarg spread to avoid a
    NULL the ORM never writes; the mutation run is what disproved it.

    The test stays because the guarantee is real and lives one layer down: it
    belongs to models.Equipment.sensitivity's default, and it breaks if anyone
    removes that default or makes the create path write NULL deliberately.
    Pinning a guarantee whose owner is elsewhere is worth doing; claiming this
    line is what could break it was not.
    """
    response = client.post(
        "/equipment/",
        json={"catalog_name": "M4", "serial_number": "NEW-H3-2-DEFAULT"},
        headers=token_master,
    )
    assert response.status_code == 200, response.text

    stored = _item(db_session, "NEW-H3-2-DEFAULT")
    assert stored.sensitivity is not None, (
        "the column is NULL: the route passed sensitivity=None explicitly and "
        "defeated the model default instead of omitting the kwarg"
    )
    assert stored.sensitivity == Sensitivity.UNCLASSIFIED.value


def test_the_verb_holder_can_create_a_classified_item_directly(
    client, mock_matrix_db, db_session, token_bat_cmdr
):
    """The create path's happy case, and the only way to get a CLASSIFIED row
    into existence in one request.

    Asserted against the stored column as well as the response, so this cannot
    pass on a response that echoes the request back without writing it.
    """
    response = client.post(
        "/equipment/",
        json={
            "catalog_name": "M4",
            "serial_number": "NEW-H3-2-CLASSIFIED",
            "sensitivity": Sensitivity.CLASSIFIED.value,
        },
        headers=token_bat_cmdr,
    )

    assert response.status_code == 200, response.text
    assert response.json()["sensitivity"] == "CLASSIFIED"
    assert _item(db_session, "NEW-H3-2-CLASSIFIED").sensitivity == "CLASSIFIED"


def test_the_route_changes_sensitivity_and_nothing_else(
    client, mock_matrix_db, db_session, token_bat_cmdr
):
    """No mass assignment, and no DATA-H5 side effect.

    Two failure modes in one test because both are "the route wrote something
    it was not asked to write".

    Extra body fields must be IGNORED, not applied. SetSensitivityRequest
    declares one field, so status and group_id here are attacker-supplied keys
    the route must never reach; a schema widened to accept them -- or a handler
    that read from the raw request instead -- would turn a classification
    endpoint into an arbitrary equipment editor with no TRANSFER gate in front
    of it. group_id is the sharper of the two: it decides who can see the item.

    last_verified_at must NOT advance. Both assign_owner and transfer_equipment
    reset it, which is exactly what DATA-H5 reports as compliance forgeable by
    paperwork; classifying an item is not laying eyes on it, so a green
    compliance badge must not fall out of this request.
    """
    item = _item(db_session, "SA100")
    before_status = item.status
    before_group = item.group_id
    before_verified = item.last_verified_at
    other_group = _item(db_session, "SB200").group_id
    assert other_group != before_group, "fixture: need a group to try to move into"

    response = client.patch(
        f"/equipment/{item.id}/sensitivity",
        json={
            "sensitivity": Sensitivity.CLASSIFIED.value,
            "group_id": other_group,
            "status": "Missing",
            "holder_user_id": None,
        },
        headers=token_bat_cmdr,
    )
    assert response.status_code == 200, response.text

    db_session.expire_all()
    fresh = _item(db_session, "SA100")
    assert fresh.sensitivity == Sensitivity.CLASSIFIED.value, "the one intended write"
    assert fresh.status == before_status, "an extra body field rewrote status"
    assert fresh.group_id == before_group, (
        "an extra body field moved the item to another group -- mass assignment, "
        "and it bypassed TRANSFER"
    )
    assert fresh.last_verified_at == before_verified, (
        "classifying advanced last_verified_at -- DATA-H5's forgeable-compliance "
        "shape at a new site"
    )


def test_classifying_is_idempotent(
    client, mock_matrix_db, db_session, token_bat_cmdr
):
    """Repeating the request is not an error and does not accumulate state.

    Cheap to assert and worth pinning before DATA-H4 adds audit writes here:
    at that point "idempotent" stops being free, and this records that the
    current answer is a plain overwrite rather than an append.
    """
    item_id = _item(db_session, "SA100").id

    for _ in range(3):
        response = _patch_sensitivity(
            client, item_id, Sensitivity.CLASSIFIED.value, token_bat_cmdr
        )
        assert response.status_code == 200, response.text

    db_session.expire_all()
    assert _item(db_session, "SA100").sensitivity == Sensitivity.CLASSIFIED.value


def test_an_unauthenticated_caller_cannot_classify(client, mock_matrix_db, db_session):
    """401 before anything else. The floor case, and it is not implied.

    get_current_active_user is a dependency rather than an in-body check, so
    this pins that the route actually declares it -- a handler written without
    it would answer 500 or, worse, 200.
    """
    item = _item(db_session, "SA100")

    response = client.patch(
        f"/equipment/{item.id}/sensitivity",
        json={"sensitivity": Sensitivity.CLASSIFIED.value},
    )

    assert response.status_code == 401, response.text
    db_session.expire_all()
    assert _item(db_session, "SA100").sensitivity == Sensitivity.UNCLASSIFIED.value


@pytest.mark.parametrize("junk", ["BANANA", "classified", "TOP_SECRET", ""])
def test_an_out_of_vocabulary_request_is_refused_at_the_door(
    client, mock_matrix_db, db_session, token_bat_cmdr, junk
):
    """422 on the way IN, and note the contrast with the response-side case.

    Request validation runs BEFORE the route, so junk is refused cleanly with
    a 422 and nothing is written. The out-of-vocabulary RESPONSE case
    (test_an_out_of_vocabulary_value_...) has to raise instead, because
    response-model validation runs after the route has already returned. Same
    enum, two different failure modes, decided by which side of the handler
    the value arrives on.

    "classified" lowercase is in the list deliberately: the enum is
    case-sensitive, and a caller who guesses the wrong case must be told so
    rather than silently classifying nothing.
    """
    item = _item(db_session, "SA100")

    response = _patch_sensitivity(client, item.id, junk, token_bat_cmdr)

    assert response.status_code == 422, (
        f"{junk!r} was accepted with {response.status_code} -- the request "
        "schema is not constraining the vocabulary"
    )
    db_session.expire_all()
    assert _item(db_session, "SA100").sensitivity == Sensitivity.UNCLASSIFIED.value


@pytest.mark.parametrize(
    "body", [{}, {"sensitivity": None}], ids=["omitted", "explicit-null"]
)
def test_an_absent_sensitivity_is_refused_rather_than_defaulted(
    client, mock_matrix_db, db_session, token_bat_cmdr, body
):
    """Neither omission nor null may stand in for a value. 422 both ways.

    This is what makes SetSensitivityRequest.sensitivity required rather than
    Optional, asserted from the outside. If the field were Optional the two
    bodies here would parse, and the route would then either write None over a
    classification -- silently declassifying an item on an empty request -- or
    quietly do nothing while answering 200. Declassifying is UNCLASSIFIED,
    stated outright, and this is the test that says so.

    Runs against an item that is already CLASSIFIED, because that is the state
    where an accidental default does damage; against an UNCLASSIFIED row a
    bug here would leave the value looking correct.
    """
    item = _item(db_session, "SA100")
    item.sensitivity = Sensitivity.CLASSIFIED.value
    db_session.commit()

    response = client.patch(
        f"/equipment/{item.id}/sensitivity", json=body, headers=token_bat_cmdr
    )

    assert response.status_code == 422, (
        f"{body!r} was accepted with {response.status_code} -- an absent value "
        "is being treated as a classification decision"
    )
    db_session.expire_all()
    assert _item(db_session, "SA100").sensitivity == Sensitivity.CLASSIFIED.value, (
        "an empty request declassified the item"
    )


# --- 5. Enforcement (DATA-H3-3) ----------------------------------------------
#
# Actors, and why each was chosen. The absences are the design, not an
# oversight -- see this module's header before "fixing" a red test by editing
# conftest's grant table:
#
#   token_bat_cmdr      holds VIEW_CLASSIFIED over 188/53 -> sees classified
#   token_company_cmdr  commands 188/53/A by VIEW, NOT VIEW_CLASSIFIED
#                       -> the core assertion: loses sight of a classified
#                          item in the company they command
#   token_soldier       HOLDS SA100, no grant of any kind
#                       -> possession survives classification
#
# SA100 lives in 188/53/A and is held by soldier_a (conftest), which is what
# makes company_cmdr_a and soldier_a a genuine pair: the same row, seen through
# two different arms of scope_equipment_query.


def _classify(db_session, serial="SA100"):
    """Classify a fixture item directly, bypassing the API.

    The write path has its own section above. Reaching past it here keeps these
    tests pinning the READ rule alone -- a failure means scoping changed, not
    that PATCH broke.
    """
    item = _item(db_session, serial)
    item.sensitivity = Sensitivity.CLASSIFIED.value
    db_session.commit()
    return item


def _fault_type(db_session):
    """A fault type to report against. conftest seeds none, so make one."""
    existing = db_session.query(models.FaultType).first()
    if existing is not None:
        return existing
    fault = models.FaultType(name="Test Fault")
    db_session.add(fault)
    db_session.commit()
    return fault


def test_a_classified_item_leaves_an_uncleared_listing(
    client, mock_matrix_db, db_session, token_company_cmdr
):
    """The core assertion of DATA-H3-3.

    company_cmdr_a commands 188/53/A and holds VIEW over it, so SA100 is in
    their listing before classification and must not be after.

    NOT parametrized over LIST_ENDPOINTS, unlike the -1 tests above, and the
    reason is a real asymmetry rather than an omission: /users/me/equipment
    does not call scope_equipment_query at all. It filters on holder_user_id
    directly (users.py), so it is a "what am I carrying" route rather than a
    scoped listing, and classification correctly never applies to it. That is
    consistent with the possession ruling, and it is pinned separately by
    test_the_holders_own_listing_is_not_filtered below rather than left as an
    inference from this test not covering it.
    """
    before = _get(client, "/equipment/accessible", token_company_cmdr)
    assert "SA100" in {row["serial_number"] for row in before}, (
        "SA100 was already invisible to the commander of its own company -- "
        "this test cannot detect anything from that starting state"
    )

    _classify(db_session)

    after = _get(client, "/equipment/accessible", token_company_cmdr)
    assert "SA100" not in {row["serial_number"] for row in after}, (
        "a CLASSIFIED item stayed visible to a caller holding VIEW but not "
        "VIEW_CLASSIFIED -- the classification clause is not being applied"
    )


def test_clearance_keeps_the_item_visible(
    client, mock_matrix_db, db_session, token_bat_cmdr
):
    """The other direction, without which the test above passes vacuously.

    token_bat_cmdr holds VIEW_CLASSIFIED over 188/53, which contains 188/53/A.
    A filter that simply hid every classified row from everyone would satisfy
    the previous test and fail this one.
    """
    _classify(db_session)

    payload = _get(client, "/equipment/accessible", token_bat_cmdr)
    assert "SA100" in {row["serial_number"] for row in payload}, (
        "a cleared caller lost sight of a classified item -- VIEW_CLASSIFIED "
        "is not widening the VIEW arm"
    )


def test_the_holders_own_listing_is_not_filtered(
    client, mock_matrix_db, db_session, token_soldier
):
    """/users/me/equipment answers "what am I carrying", so it never filters.

    Pinned explicitly because the route reaches this outcome by a DIFFERENT
    mechanism than every other listing: it queries holder_user_id directly and
    never calls scope_equipment_query, so the classification clause is not
    bypassed there so much as absent. The result agrees with the possession
    ruling, which is why it is correct rather than a hole -- but an agreement
    reached by two unrelated code paths is exactly the kind that drifts, and
    DATA-H9 is the ticket about this route's hand-built duplication.

    If someone later routes this endpoint through the shared scope function --
    a reasonable DATA-H9 cleanup -- this test should still pass, because the
    holder arm survives classification. If it goes red, that refactor changed
    behaviour.
    """
    _classify(db_session)

    payload = _get(client, "/users/me/equipment", token_soldier)
    assert "SA100" in {row["serial_number"] for row in payload}, (
        "the holder lost their own classified item from /users/me/equipment"
    )


# Possession-survives-classification is NOT restated here, deliberately.
# test_sensitivity_does_not_widen_or_narrow_scope above already asserts exactly
# that -- same actor (token_soldier, who holds SA100 and no grant), same
# endpoint, same CLASSIFIED setup -- and additionally checks that Company B's
# item stays hidden. A section-5 copy was written and then deleted as a strict
# subset of it; one pin, in the place whose whole identity is that ruling.
# test_the_holder_can_still_report_a_fault_on_a_classified_item below is the
# non-redundant half: it covers the consequence, not the visibility.


def test_an_uncleared_caller_gets_404_not_403_by_id(
    client, mock_matrix_db, db_session, token_company_cmdr
):
    """The resolver inherits the rule, and stays a non-oracle.

    404 rather than 403 matters more here than anywhere else in the codebase.
    company_cmdr_a lacks SET_SENSITIVITY, so before this ticket this exact
    request answered 403 -- see test_commanding_the_item_is_not_enough_without
    _the_verb in section 4, which still asserts that for an UNCLASSIFIED item.
    Once the item is classified the answer must change to 404, because a 403
    would confirm both that an item exists at that id and that it is classified
    -- precisely the two facts the ticket hides.

    That flip is what makes this test load-bearing rather than decorative: it
    pins the resolve-then-decide ORDER through a route that genuinely calls
    get_scoped_equipment_or_404, so a future edit that asks the verb first
    turns a 404 back into an oracle and this goes red.

    Uses PATCH deliberately. There is no GET /equipment/{id} in this codebase
    -- an earlier draft of this test asserted 404 against that path and passed
    for the worthless reason that FastAPI 404s any unrouted URL. It survived
    the whole suite and was caught only by the mutation that made the resolver
    bypass scoping, which left it green. A 404 assertion is only evidence when
    the route exists.
    """
    item = _classify(db_session)

    response = _patch_sensitivity(
        client, item.id, Sensitivity.UNCLASSIFIED.value, token_company_cmdr
    )
    assert response.status_code == 404, (
        f"expected 404 for a classified item out of clearance, got "
        f"{response.status_code}: {response.text} -- a 403 here means the verb "
        f"was asked before the resolver, which is an id-enumeration oracle"
    )


def test_the_holder_can_still_report_a_fault_on_a_classified_item(
    client, mock_matrix_db, db_session, token_soldier
):
    """The reason the holder arm was left outside the clause.

    This is the failure mode that ruled out the stricter design. Hiding the
    item from its holder would make get_scoped_equipment_or_404 raise 404 for
    them, and require_status_authority's possession arm -- the thing that lets
    a private report that the rifle in their hands is broken -- could never be
    reached. Classification would have silently removed a soldier's ability to
    report their own equipment unserviceable.
    """
    item = _classify(db_session)

    response = client.post(
        "/maintenance/report",
        json={
            "equipment_id": item.id,
            "fault_name": _fault_type(db_session).name,
            "description": "classified item, still broken",
        },
        headers=token_soldier,
    )
    assert response.status_code in (200, 201), (
        f"a holder could not report a fault on the classified item they carry: "
        f"{response.status_code} {response.text}"
    )


def test_a_null_sensitivity_stays_visible_to_an_uncleared_caller(
    client, mock_matrix_db, db_session, token_company_cmdr
):
    """The three-valued-logic trap, and the subtlest thing in the ticket.

    sensitivity is a nullable String until DATA-H12 constrains it, and a NULL
    is reachable by hand-written SQL today -- see
    test_a_null_sensitivity_does_not_take_down_the_whole_list above, which
    writes one the same way.

    Under SQL's three-valued logic `NULL != 'CLASSIFIED'` is NULL, not TRUE, so
    a bare `!=` in the scope filter would drop this row for every uncleared
    caller. That fails CLOSED, which sounds like the safe direction and is not:
    DATA-H3-1 chose deliberately to surface an out-of-vocabulary value loudly
    rather than let it pass as UNCLASSIFIED, and silently vanishing the row
    reinstates the quiet-wrong-answer behaviour that ticket existed to end.
    dependencies.scope_equipment_query uses IS DISTINCT FROM for this reason.

    The row is fetched through /reports/query rather than the list routes
    because a NULL sensitivity fails EquipmentResponse validation -- that is
    the -1 behaviour pinned above, and this test is about SCOPING rather than
    serialization.
    """
    item = _item(db_session, "SA100")
    db_session.execute(
        text("UPDATE equipment SET sensitivity = NULL WHERE id = :id"),
        {"id": item.id},
    )
    db_session.commit()

    payload = _get(client, "/reports/query", token_company_cmdr)
    assert "SA100" in {row["serial_number"] for row in payload}, (
        "a NULL sensitivity was treated as CLASSIFIED and hidden -- the scope "
        "filter is using `!= CLASSIFIED` instead of IS DISTINCT FROM"
    )


def test_derived_listings_inherit_the_rule(
    client, mock_matrix_db, db_session, token_company_cmdr
):
    """Maintenance tickets and transaction logs follow the item.

    Neither route mentions sensitivity. Both scope through
    scope_equipment_derived_query, which joins to Equipment and defers to the
    same filter, so classifying the ITEM must remove the rows ABOUT it. This is
    the blast radius that made enforcement its own ticket rather than a clause
    in DATA-H3-2 -- asserted here rather than assumed.
    """
    item = _item(db_session, "SA100")
    db_session.add(models.MaintenanceLog(
        equipment_id=item.id,
        fault_type_id=_fault_type(db_session).id,
        description="visible before classification",
        status="Open",
    ))
    db_session.add(models.TransactionLog(
        equipment_id=item.id, event_type="TEST_EVENT",
    ))
    db_session.commit()

    tickets = _get(client, "/tickets/", token_company_cmdr)
    logs = _get(client, "/reports/daily_movement", token_company_cmdr)
    assert any(t["equipment_id"] == item.id for t in tickets), (
        "the ticket was not visible before classification -- this test cannot "
        "detect anything from that starting state"
    )
    assert logs, "no transaction logs visible before classification"

    _classify(db_session)

    tickets_after = _get(client, "/tickets/", token_company_cmdr)
    logs_after = _get(client, "/reports/daily_movement", token_company_cmdr)
    assert not any(t["equipment_id"] == item.id for t in tickets_after), (
        "a maintenance ticket for a classified item stayed visible to an "
        "uncleared caller -- scope_equipment_derived_query is not inheriting "
        "the classification clause"
    )
    assert len(logs_after) < len(logs), (
        "a transaction log for a classified item stayed visible to an "
        "uncleared caller"
    )


def test_analytics_totals_shrink_for_an_uncleared_caller(
    client, mock_matrix_db, db_session, token_company_cmdr
):
    """The accepted leak, pinned so it cannot change silently.

    analytics.unit_readiness counts through the same scope, so classifying an
    item in the caller's scope drops it from the denominator. That is the
    ruling recorded in that route's comment -- the number means "the readiness
    of what you can see" -- and NOT an oversight.

    Pinned in both directions on purpose. If someone later routes analytics
    around the classification filter to keep the number "truthful", this test
    goes red and they must change the documented decision deliberately rather
    than discover it. If instead the filter silently stops applying, it also
    goes red.

    The cost this accepts, stated plainly: a cleared and an uncleared caller
    comparing totals can infer that a classified item exists in that scope,
    though not which one.
    """
    before = _get(client, "/analytics/unit_readiness", token_company_cmdr)
    _classify(db_session)
    after = _get(client, "/analytics/unit_readiness", token_company_cmdr)

    assert after["total_items"] == before["total_items"] - 1, (
        f"expected the uncleared caller's total to drop by exactly one "
        f"(got {before['total_items']} -> {after['total_items']})"
    )


def test_clearance_without_view_confers_nothing(
    client, mock_matrix_db, db_session, token_company_cmdr
):
    """Clearance WIDENS sight you already have; it does not confer sight.

    company_cmdr_a has no authority over Company B, and granting them
    VIEW_CLASSIFIED over that company must not hand them its inventory. This is
    the reason VIEW_CLASSIFIED is deliberately excluded from
    authz.EQUIPMENT_CAPABILITIES: were it included, implied_view_placements
    would derive a VIEW grant from this one and quietly turn it into exactly
    the widening this test forbids.
    """
    _classify(db_session, "SB200")
    group_b = db_session.query(authz.Group).filter(
        authz.Group.name == "188/53/B"
    ).one()
    cmdr = db_session.query(models.User).filter(
        models.User.personal_number == "u_cmdr_a"
    ).one()
    db_session.add(authz.Grant(
        user_id=cmdr.id,
        group_id=group_b.id,
        capability=Capability.VIEW_CLASSIFIED.value,
    ))
    db_session.commit()

    payload = _get(client, "/equipment/accessible", token_company_cmdr)
    assert "SB200" not in {row["serial_number"] for row in payload}, (
        "VIEW_CLASSIFIED alone made another company's item visible -- "
        "clearance is conferring sight rather than widening it"
    )


# --- 6. Enforcement, the surfaces reached indirectly (DATA-H3-3) --------------
#
# Section 5 pins the rule at the two places it is written: the listing and the
# resolver. These pin that it ARRIVES everywhere those two are consumed, which
# is the claim scope_equipment_query's docstring actually makes and the reason
# enforcement was worth its own ticket. Every test below was found by probing
# the running app for a way to learn that a classified item exists.


@pytest.mark.parametrize("method,path,body", [
    ("get", "/verifications/equipment/{id}", None),
    ("get", "/equipment/{id}/history", None),
    ("post", "/equipment/{id}/verify", {}),
])
def test_every_by_id_route_answers_404_for_a_classified_item(
    client, mock_matrix_db, db_session, token_company_cmdr, method, path, body
):
    """Routes taking the id in the PATH inherit the rule, and answer 404.

    None of these three mentions sensitivity. Each calls
    get_scoped_equipment_or_404 and therefore picks up the classification
    clause for free -- but "for free" is precisely the kind of claim that stops
    being true when someone adds a fourth route resolving by raw id, so it is
    asserted per route rather than argued once.

    The status-history and verification-list routes matter most here. Both
    return the full audit trail of an item, naming the user behind every change,
    and both were unscoped until H1-9. A leak at either would disclose more
    about a classified item than the listing ever would.

    THE PRE-CHECK IS NOT CEREMONY. A 404 assertion against a URL that does not
    exist passes for the worst possible reason -- FastAPI 404s any unrouted
    path -- and this suite has now produced that mistake twice: once against a
    GET /equipment/{id} that was never a route, and once here, where the
    verification list is /verifications/equipment/{id} rather than the
    /equipment/{id}/verifications this table first guessed. Both survived the
    whole suite and were caught only by a mutation leaving them green. So each
    case first proves the route answers something OTHER than 404 while the item
    is unclassified; only then is the 404 evidence of scoping.
    """
    item = _item(db_session, "SA100")
    url = path.format(id=item.id)

    def call():
        if body is None:
            return client.get(url, headers=token_company_cmdr)
        return client.post(
            url, json=dict(body, equipment_id=item.id), headers=token_company_cmdr
        )

    before = call()
    assert before.status_code != 404, (
        f"{method.upper()} {url} already answers 404 while the item is "
        f"UNCLASSIFIED -- the path does not exist, so the assertion below "
        f"would pass without testing anything"
    )

    _classify(db_session)

    response = call()
    assert response.status_code == 404, (
        f"{method.upper()} {url} answered {response.status_code} for a "
        f"classified item out of clearance: {response.text[:200]}"
    )


@pytest.mark.parametrize("path,body", [
    ("/equipment/transfer", {"to_holder_id": 1}),
    ("/equipment/assign_owner/", {"owner_id": 1}),
])
def test_write_routes_taking_the_id_in_the_body_also_answer_404(
    client, mock_matrix_db, db_session, token_company_cmdr, path, body
):
    """The same, for the older routes that take equipment_id in the BODY.

    Worth separating from the path-parameter cases above because these are the
    routes H1-9 had to FIX: each once carried its own raw-id lookup and answered
    403 for an item the caller could not see, which is the enumeration oracle
    get_scoped_equipment_or_404 exists to close. A classified item makes that
    oracle strictly worse -- a 403 would confirm both that the id is real and
    that it is classified.

    company_cmdr_a holds TRANSFER over this item's company, so before
    classification these routes answer on their own merits; only the
    classification turns them into 404. That is what makes this a test of the
    scope filter rather than of the transfer gate.

    A malformed body is NOT tested here, on purpose. transfer answers 400 from
    an XOR check that reads the request only, ahead of the resolver, and that
    400 is identical for every caller and every id (see the comment on that
    route). It discloses nothing, so it is correct rather than a hole -- the
    adversarial probe that produced this test flagged it only because the probe
    itself sent the wrong field name.
    """
    item = _item(db_session, "SA100")
    payload = dict(body, equipment_id=item.id)

    before = client.post(path, json=payload, headers=token_company_cmdr)
    assert before.status_code != 404, (
        f"POST {path} already answers 404 while the item is UNCLASSIFIED -- "
        f"the path or body is wrong, so the assertion below would pass without "
        f"testing anything (see the sibling test above for why this guard is here)"
    )

    _classify(db_session)

    response = client.post(path, json=payload, headers=token_company_cmdr)
    assert response.status_code == 404, (
        f"POST {path} answered {response.status_code} for a classified item "
        f"out of clearance: {response.text[:200]}"
    )


def test_the_readiness_numerator_is_filtered_too_not_just_the_total(
    client, mock_matrix_db, db_session, token_company_cmdr
):
    """analytics runs scope_equipment_query TWICE, and both calls must filter.

    The route computes functional/total from two separate queries. Section 5
    pins the denominator; this pins the numerator, because a filter applied to
    only one of them would not hide anything -- it would silently CORRUPT the
    percentage, reporting a readiness figure computed from mismatched
    populations. That is a worse outcome than either extreme, and it would
    surface as a plausible-looking number rather than as an error.
    """
    before = _get(client, "/analytics/unit_readiness", token_company_cmdr)
    assert before["functional_items"] > 0, (
        "no functional items visible before classification -- this test cannot "
        "detect anything from that starting state"
    )

    _classify(db_session)

    after = _get(client, "/analytics/unit_readiness", token_company_cmdr)
    assert after["functional_items"] == before["functional_items"] - 1, (
        f"the functional count did not follow the total "
        f"({before['functional_items']} -> {after['functional_items']}) -- the "
        f"two scope calls in analytics.py have diverged"
    )


def test_implied_view_does_not_smuggle_clearance_with_it(
    client, mock_matrix_db, db_session, token_brigade_tech
):
    """A holder of VIEW-by-derivation is still uncleared.

    brigade_tech appears in NO literal VIEW row: their sight comes entirely
    from authz.implied_view_placements, derived from their REPORT_STATUS and
    RESOLVE_FAULT grants. Every other test in this section uses an actor whose
    VIEW is a written table row, so this is the one that exercises the
    derivation path -- the filter must narrow a derived VIEW exactly as it
    narrows a granted one.

    Pairs with test_clearance_without_view_confers_nothing above: that one
    proves clearance does not confer sight, this one proves a sight obtained by
    derivation does not arrive pre-cleared.

    What it does NOT detect, corrected after measuring rather than assuming:
    adding VIEW_CLASSIFIED to authz.EQUIPMENT_CAPABILITIES. An earlier draft of
    this docstring claimed that as the point, and the mutation proved otherwise
    -- that tuple makes a verb imply VIEW, so including VIEW_CLASSIFIED would
    derive VIEW *from* clearance, not clearance from REPORT_STATUS. It cannot
    reach this user at all. The guard that does catch that mutation is
    test_implied_view_covers_every_equipment_verb in tests/test_query_surface.py,
    which asserts the tuple's exact membership.
    """
    before = _get(client, "/equipment/accessible", token_brigade_tech)
    assert "SA100" in {row["serial_number"] for row in before}, (
        "the brigade tech could not see SA100 before classification -- this "
        "test cannot detect anything from that starting state"
    )

    _classify(db_session)

    after = _get(client, "/equipment/accessible", token_brigade_tech)
    assert "SA100" not in {row["serial_number"] for row in after}, (
        "a user whose VIEW is derived by implied_view_placements also received "
        "clearance -- VIEW_CLASSIFIED has leaked into EQUIPMENT_CAPABILITIES"
    )


def test_a_report_filter_cannot_be_used_to_confirm_the_item(
    client, mock_matrix_db, db_session, token_company_cmdr
):
    """A user-supplied filter narrows within the scope and cannot reach around it.

    What this pins: the scope predicate and the caller's filters COMPOSE by
    conjunction, so a filter can only ever remove rows the scope allowed. It
    would catch a filter combined with `or_`, or a rewrite that rebuilt the
    query object instead of chaining onto the scoped one -- either would let a
    search term surface a row the scope had removed.

    What it does NOT pin, stated because an earlier draft of this docstring
    claimed the opposite and was wrong: the ORDER of the .filter() calls in
    reports.py. Moving the scope call below the user filters was tried as a
    mutation and left the entire suite green -- correctly, because SQLAlchemy
    accumulates .filter() calls into one AND conjunction and AND is
    commutative. The two orderings compile to the same predicate and return the
    same rows, so there is no ordering bug there to detect. Verified directly
    against SQLAlchemy rather than reasoned about, after the same class of
    assumption about this ORM produced a wrong comment in DATA-H3-2.

    Ordering IS load-bearing elsewhere in this ticket -- resolve-then-decide,
    pinned by the 404 tests above -- which is where that intuition belongs.
    """
    _classify(db_session)

    payload = _get(client, "/reports/query?status=Functional", token_company_cmdr)
    assert "SA100" not in {row["serial_number"] for row in payload}, (
        "a status filter surfaced a classified item the scope had removed -- "
        "the filters are being applied before the scope in reports.py"
    )
