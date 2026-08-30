"""DATA-H3-1: equipment responses report the sensitivity the record holds.

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

WHAT THIS TICKET DOES NOT DO
----------------------------
Nothing here enforces anything. Sensitivity is not consulted by
scope_equipment_query or any gate, and CLASSIFIED remains unreachable through
the API. DATA-H3-2 adds the write path and the scoping enforcement. These tests
pin a REPORTING contract -- that the field tracks the column -- and
test_sensitivity_does_not_widen_or_narrow_scope exists to catch anyone who
quietly makes it a visibility axis before that ticket says so.
"""
import pytest
from pydantic import ValidationError
from sqlalchemy import text

from backend import models
from backend.enums import Sensitivity

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
    """Adding a field must not add or remove rows.

    Two directions, both worth pinning. A CLASSIFIED item the soldier holds
    must still be VISIBLE -- sensitivity is not a gate in this ticket, and a
    future edit that starts filtering on it would silently hide equipment from
    the person carrying it. And Company B's item must stay INVISIBLE, so the
    response gains a column rather than a neighbour's inventory.

    DATA-H3-2 is where sensitivity may legitimately affect visibility. Until
    then this failing is the signal that enforcement arrived without its
    ticket, its capability, or its grants.
    """
    a_item = _item(db_session, "SA100")
    a_item.sensitivity = Sensitivity.CLASSIFIED.value
    db_session.commit()

    payload = _get(client, "/equipment/accessible", token_soldier)
    serials = {row["serial_number"] for row in payload}

    assert "SA100" in serials, (
        "a CLASSIFIED item vanished from its own holder's listing -- "
        "sensitivity has become a filter, which is DATA-H3-2's job"
    )
    assert "SB200" not in serials, "a Company B item leaked into Company A's listing"
