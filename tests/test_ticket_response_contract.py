"""DATA-H2: /tickets/ carries the open date it has always read from the database.

The defect: `TicketResponse` declared `created_at` ("Alias for timestamp") and
`timestamp` ("DB field name") -- two names for a column actually called
`opened_at` (models.py:238) -- and the route passed neither. It passed
`opened_at=`, which the schema did not declare, and Pydantic v2 discards unknown
`__init__` kwargs silently. So every ticket in the force went out with two null
date fields and no open date at all, while MaintenancePage.tsx typed `opened_at`
as a guaranteed string and rendered a permanent em-dash for it.

Three-way drift: a real column, a schema that omitted it, a frontend type that
required it. The fix declares the column's real name and deletes both aliases.

WHY `opened_at` IS OPTIONAL AND NOT REQUIRED
--------------------------------------------
`maintenance_logs.opened_at` is nullable in the schema (4acc9d5f6339:143). The
model supplies `default=clock.utcnow`, but that is a PYTHON-side default -- it
fires on an ORM insert and not on a raw one, so NULL is reachable in any
database this code has ever touched. A required field would fail validation on
one such row and take the ENTIRE list response down with it, which is DATA-M12's
shape exactly. test_a_null_opened_at_does_not_take_down_the_whole_list is the
pin on that reasoning: it fails if anyone tightens the field to required, which
is the point of writing it.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from backend import clock, models

REPORT_FAULT = {"fault_name": "Contract Fault", "description": "smoke pouring out"}


def _open_ticket(client, token, equipment_id, **overrides):
    """Report a fault through the real route and return the created ticket id.

    Deliberately goes through HTTP rather than inserting a MaintenanceLog
    directly: the thing under test is what the ROUTE emits, and a fixture that
    builds the row itself would not exercise report_fault's insert path -- the
    only path that populates opened_at in production.
    """
    payload = {"equipment_id": equipment_id, **REPORT_FAULT, **overrides}
    response = client.post("/maintenance/report", json=payload, headers=token)
    assert response.status_code == 200, response.text
    return response.json()["ticket_id"]


def _tickets(client, token):
    response = client.get("/tickets/", headers=token)
    assert response.status_code == 200, response.text
    return response.json()


def _item_id(db_session, serial):
    return (
        db_session.query(models.Equipment)
        .filter(models.Equipment.serial_number == serial)
        .one()
        .id
    )


# --- 1. The field exists and carries the row's value -------------------------


def test_ticket_response_carries_opened_at(client, mock_matrix_db, db_session, token_master):
    """The bug, stated as the smallest possible assertion.

    Before the fix this failed on the `in` check -- the key was absent from the
    payload entirely, not present-and-null, because Pydantic dropped the kwarg
    rather than defaulting it.
    """
    item_id = _item_id(db_session, "SA100")
    _open_ticket(client, token_master, item_id)

    ticket = _tickets(client, token_master)[0]

    assert "opened_at" in ticket, f"opened_at missing from payload: {sorted(ticket)}"
    assert ticket["opened_at"] is not None


def test_opened_at_is_the_stored_value_not_a_recomputed_now(
    client, mock_matrix_db, db_session, token_master
):
    """Proves the field is READ FROM THE RECORD, not regenerated at serialization.

    A schema that defaulted `opened_at` to `clock.utcnow` would pass the test
    above while reporting the moment of the READ rather than the moment the
    fault was reported -- a subtler version of the same lie. Backdating the
    stored row by a week separates the two: only a value sourced from the row
    can come back a week old.
    """
    item_id = _item_id(db_session, "SA100")
    ticket_id = _open_ticket(client, token_master, item_id)

    backdated = clock.utcnow() - timedelta(days=7)
    log = db_session.query(models.MaintenanceLog).filter_by(id=ticket_id).one()
    log.opened_at = backdated
    db_session.commit()

    ticket = next(t for t in _tickets(client, token_master) if t["id"] == ticket_id)

    emitted = datetime.fromisoformat(ticket["opened_at"])
    assert emitted.tzinfo is not None, "DATA-H1: the wire value must be aware"
    assert abs((emitted - backdated).total_seconds()) < 1, (
        f"emitted {emitted} is not the stored {backdated} -- "
        "the field is being recomputed rather than read"
    )


# --- 2. The aliases are gone -------------------------------------------------


@pytest.mark.parametrize("alias", ["created_at", "timestamp"])
def test_the_unpopulated_date_aliases_are_absent(
    client, mock_matrix_db, db_session, token_master, alias
):
    """Pins the REMOVAL, which nothing else would notice.

    Both aliases serialized as null on every ticket ever returned. Without this
    test a revert that restores them is invisible: the payload simply grows two
    null columns again and every other assertion in this file still passes.
    """
    item_id = _item_id(db_session, "SA100")
    _open_ticket(client, token_master, item_id)

    ticket = _tickets(client, token_master)[0]

    assert alias not in ticket, (
        f"{alias!r} is back on TicketResponse -- it names the opened_at column "
        "under a name the route never passes, so it can only ever be null"
    )


# --- 3. The reason the field is Optional -------------------------------------


def test_a_null_opened_at_does_not_take_down_the_whole_list(
    client, mock_matrix_db, db_session, token_master
):
    """The DATA-M12 guard, and the justification for Optional over required.

    A raw UPDATE is used rather than the ORM to reach a state the Python-side
    `default=clock.utcnow` cannot be talked out of producing. This is not a
    contrived state: any pre-existing row, any bulk import, any INSERT not going
    through the ORM lands here, and the column permits it.

    The assertion that matters is the SECOND ticket. A required `opened_at`
    fails validation on the null row, and because FastAPI validates the response
    model over the whole list, the caller loses every OTHER ticket too -- one
    bad row blanks the maintenance page rather than one line of it.
    """
    item_id = _item_id(db_session, "SA100")
    nulled = _open_ticket(client, token_master, item_id)
    intact = _open_ticket(client, token_master, item_id, fault_name="Second Fault")

    db_session.execute(
        text("UPDATE maintenance_logs SET opened_at = NULL WHERE id = :id"),
        {"id": nulled},
    )
    db_session.commit()

    tickets = _tickets(client, token_master)
    by_id = {t["id"]: t for t in tickets}

    assert nulled in by_id, "the null-dated ticket vanished from the list"
    assert by_id[nulled]["opened_at"] is None
    assert intact in by_id, (
        "a single null opened_at removed an unrelated ticket from the response "
        "-- opened_at has been tightened to required, see this module's docstring"
    )
    assert by_id[intact]["opened_at"] is not None


def test_ordering_survives_a_null_opened_at(
    client, mock_matrix_db, db_session, token_master
):
    """maintenance.py:47 orders by opened_at desc, which now has a null to sort.

    Separate from the test above because it exercises the SQL rather than the
    serializer: an ORDER BY over a nullable column is backend-defined (SQLite
    and Postgres disagree on where nulls land), and the contract being pinned
    is only that the query still returns every row. Asserting a specific
    position would encode SQLite's convention into a suite that also runs
    against Postgres.

    Verified non-redundant rather than assumed so: adding a
    `.filter(opened_at.isnot(None))` to maintenance.py:47 turns this red
    independently of the test above, and it does so with three rows around the
    null rather than two -- a row silently dropped by the sort is not something
    the two-row serializer test is shaped to see.
    """
    item_id = _item_id(db_session, "SA100")
    first = _open_ticket(client, token_master, item_id)
    second = _open_ticket(client, token_master, item_id, fault_name="Second Fault")
    third = _open_ticket(client, token_master, item_id, fault_name="Third Fault")

    db_session.execute(
        text("UPDATE maintenance_logs SET opened_at = NULL WHERE id = :id"),
        {"id": second},
    )
    db_session.commit()

    returned = {t["id"] for t in _tickets(client, token_master)}
    assert returned == {first, second, third}


# --- 4. The field that already worked ----------------------------------------


def test_closed_at_still_populated_after_a_fix(
    client, mock_matrix_db, db_session, token_master
):
    """Regression guard on the one date the schema already declared correctly.

    `closed_at` was never part of the defect, which is exactly why it is worth
    a test here: the fix edits the two lines directly above it, and a slip that
    dropped or renamed it would otherwise be caught by nothing in this suite.
    """
    item_id = _item_id(db_session, "SA100")
    ticket_id = _open_ticket(client, token_master, item_id)

    assert _tickets(client, token_master)[0]["closed_at"] is None

    fix = client.post(f"/maintenance/fix/{item_id}", headers=token_master)
    assert fix.status_code == 200, fix.text

    ticket = next(t for t in _tickets(client, token_master) if t["id"] == ticket_id)
    assert ticket["closed_at"] is not None
    assert ticket["status"] == "Closed"
    assert ticket["opened_at"] is not None, "closing a ticket must not clear its open date"


# The wire FORMAT of opened_at is deliberately not pinned here. A test
# comparing its zone designator against closed_at's would be a tautology of the
# serialization layer: both are Optional[datetime] on one model, loaded through
# one clock.UtcDateTime and encoded by one JSON encoder, so no realistic edit
# makes them disagree while leaving both present. The real guard is
# test_utc_contract.py's sweep, which now collects opened_at and checks its
# designator against EVERY other endpoint -- strictly stronger, and not vacuous.


# --- 5. Scope is unchanged ---------------------------------------------------


def test_opened_at_does_not_widen_who_can_see_a_ticket(
    client, mock_matrix_db, db_session, token_soldier
):
    """Adding a field must not add rows.

    /tickets/ is scoped through the equipment each ticket is about (SEC-H5,
    closed at H1-10.5). Soldier A holds exactly one item, so a fault opened on
    Company B's item must stay invisible -- the response gains a column, not a
    neighbour's maintenance history.
    """
    a_item = _item_id(db_session, "SA100")
    b_item = _item_id(db_session, "SB200")

    # Opened by the soldier on their own item -- the only fault they may report.
    _open_ticket(client, token_soldier, a_item)

    # A fault on Company B's item, inserted directly: soldier_b's token would
    # work too, but the point is the READ scope, and building the row avoids
    # asserting anything about who may write it.
    db_session.add(
        models.MaintenanceLog(
            equipment_id=b_item, description="not yours", status="Open",
            opened_at=clock.utcnow(),
        )
    )
    db_session.commit()

    visible = {t["equipment_id"] for t in _tickets(client, token_soldier)}
    assert visible == {a_item}, "a Company B ticket leaked into Company A's list"
