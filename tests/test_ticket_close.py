"""DATA-M21: a ticket is closed by its own id, not by its item.

The maintenance page's per-ticket button used to post
POST /maintenance/fix/{equipment_id}, which closed EVERY open ticket of the
item and declared it repaired. POST /maintenance/tickets/{ticket_id}/close
closes the one ticket; the item turns Functional only with its last open
ticket. The item route stays as the explicit whole-item repair.

Tickets are opened through POST /maintenance/report rather than built by hand,
so each one comes with the Malfunctioning status, history row and FAULT event
a real ticket has, and the counts below start from what production would hold.
"""
import pytest
from sqlalchemy import text

from backend import models
from backend.enums import ChangeReason, EquipmentStatus, EventType, TicketStatus
from tests.conftest import create_auth_header


def item_named(db, serial):
    return db.query(models.Equipment).filter_by(serial_number=serial).one()


def user_named(db, personal_number):
    return db.query(models.User).filter_by(personal_number=personal_number).one()


def open_ticket(client, item_id, fault="Cracked Housing", who="u_soldier_a"):
    res = client.post(
        "/maintenance/report",
        json={"equipment_id": item_id, "fault_name": fault, "description": "found on parade"},
        headers=create_auth_header(who),
    )
    assert res.status_code == 200, res.text
    return res.json()["ticket_id"]


def close(client, ticket_id, who="u_tech_a"):
    return client.post(
        f"/maintenance/tickets/{ticket_id}/close", headers=create_auth_header(who)
    )


def ticket(db, ticket_id):
    db.expire_all()
    return db.get(models.MaintenanceLog, ticket_id)


def history_rows(db, item_id):
    return db.query(models.EquipmentStatusHistory).filter_by(equipment_id=item_id).all()


def events(db, item_id, event_type=None):
    q = db.query(models.TransactionLog).filter_by(equipment_id=item_id)
    if event_type is not None:
        q = q.filter_by(event_type=event_type.value)
    return q.all()


def snapshot(db, item_id):
    """Everything a close may write, so a refused one can be shown to write none."""
    db.expire_all()
    item = db.get(models.Equipment, item_id)
    tickets = (
        db.query(models.MaintenanceLog)
        .filter_by(equipment_id=item_id)
        .order_by(models.MaintenanceLog.id)
        .all()
    )
    return (
        item.status,
        [(t.id, t.status, t.closed_at, t.technician_id) for t in tickets],
        len(history_rows(db, item_id)),
        len(events(db, item_id)),
    )


# --- one of several --------------------------------------------------------


def test_closing_one_of_two_tickets_closes_only_that_one(client, db_session, mock_matrix_db):
    """The defect itself: the sibling used to close with it, and the item was
    declared repaired while a second fault was still open."""
    item = item_named(db_session, "SA100")
    first = open_ticket(client, item.id, "Cracked Housing")
    second = open_ticket(client, item.id, "Dead Battery")
    history_before = len(history_rows(db_session, item.id))
    events_before = len(events(db_session, item.id))
    tech = user_named(db_session, "u_tech_a")

    res = close(client, first)

    assert res.status_code == 200, res.text
    closed = ticket(db_session, first)
    assert closed.status == TicketStatus.CLOSED.value
    assert closed.closed_at is not None
    assert closed.technician_id == tech.id

    sibling = ticket(db_session, second)
    assert sibling.status == TicketStatus.OPEN.value
    assert sibling.closed_at is None
    assert sibling.technician_id is None

    assert db_session.get(models.Equipment, item.id).status == EquipmentStatus.MALFUNCTIONING.value
    # Nothing about the item changed, so neither audit table has anything to say.
    assert len(history_rows(db_session, item.id)) == history_before
    assert len(events(db_session, item.id)) == events_before


def test_closing_the_last_open_ticket_repairs_the_item(client, db_session, mock_matrix_db):
    item = item_named(db_session, "SA100")
    first = open_ticket(client, item.id, "Cracked Housing")
    second = open_ticket(client, item.id, "Dead Battery")
    tech = user_named(db_session, "u_tech_a")
    assert close(client, first).status_code == 200
    repairs_before = [
        r for r in history_rows(db_session, item.id) if r.change_reason == ChangeReason.REPAIR.value
    ]
    fixes_before = events(db_session, item.id, EventType.FIX)
    assert repairs_before == [] and fixes_before == []

    res = close(client, second)

    assert res.status_code == 200, res.text
    db_session.expire_all()
    assert db_session.get(models.Equipment, item.id).status == EquipmentStatus.FUNCTIONAL.value

    repairs = [
        r for r in history_rows(db_session, item.id) if r.change_reason == ChangeReason.REPAIR.value
    ]
    assert len(repairs) == 1
    assert repairs[0].old_status == EquipmentStatus.MALFUNCTIONING.value
    assert repairs[0].new_status == EquipmentStatus.FUNCTIONAL.value
    assert repairs[0].created_by == tech.id

    fixes = events(db_session, item.id, EventType.FIX)
    assert len(fixes) == 1
    assert fixes[0].involved_user_id == tech.id


def test_a_single_ticket_is_its_items_last(client, db_session, mock_matrix_db):
    """The common case: one fault, one ticket, one close, item repaired."""
    item = item_named(db_session, "SA100")
    only = open_ticket(client, item.id)

    assert close(client, only).status_code == 200

    db_session.expire_all()
    assert db_session.get(models.Equipment, item.id).status == EquipmentStatus.FUNCTIONAL.value
    assert len(events(db_session, item.id, EventType.FIX)) == 1


def test_a_ticket_of_another_item_does_not_count_as_still_open(client, db_session, mock_matrix_db):
    """The remaining count is per ITEM. An open ticket on a different item in
    the same company must not hold this one Malfunctioning."""
    item = item_named(db_session, "SA100")
    other = item_named(db_session, "TA300")
    mine = open_ticket(client, item.id)
    open_ticket(client, other.id, who="u_tech_a")

    assert close(client, mine).status_code == 200

    db_session.expire_all()
    assert db_session.get(models.Equipment, item.id).status == EquipmentStatus.FUNCTIONAL.value
    assert db_session.get(models.Equipment, other.id).status == EquipmentStatus.MALFUNCTIONING.value


@pytest.mark.parametrize("status", [TicketStatus.IN_PROGRESS, TicketStatus.WAITING_PARTS])
def test_a_ticket_in_any_unclosed_status_counts_and_closes(
    status, client, db_session, mock_matrix_db
):
    """start_ticket writes the first (DATA-M22), nothing writes the second,
    and the constraint admits both. Set by raw SQL here so this stays about
    the close. A sibling in either keeps the item broken, and either can be
    closed."""
    item = item_named(db_session, "SA100")
    first = open_ticket(client, item.id, "Cracked Housing")
    second = open_ticket(client, item.id, "Dead Battery")
    db_session.execute(
        text("UPDATE maintenance_logs SET status = :s WHERE id = :id"),
        {"s": status.value, "id": second},
    )
    db_session.commit()

    assert close(client, first).status_code == 200
    db_session.expire_all()
    assert db_session.get(models.Equipment, item.id).status == EquipmentStatus.MALFUNCTIONING.value

    res = close(client, second)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == TicketStatus.CLOSED.value
    db_session.expire_all()
    assert db_session.get(models.Equipment, item.id).status == EquipmentStatus.FUNCTIONAL.value


def _null_the_status(db, ticket_id):
    """The column is nullable and its default is Python-side only, so raw SQL
    can store NULL, and ck_maintenance_logs_status lets NULL through (DATA-M12
    owns the nullability)."""
    db.execute(
        text("UPDATE maintenance_logs SET status = NULL WHERE id = :id"), {"id": ticket_id}
    )
    db.commit()


def test_a_ticket_with_no_status_is_closed_not_skipped(client, db_session, mock_matrix_db):
    """`status != 'Closed'` is NULL, not TRUE, for a NULL status, so a bare `!=`
    closed nothing and counted nothing: the route repaired the item, committed,
    and then failed building the response from the still-NULL ticket. NULL
    counts as not closed, as dependencies.scope_equipment_query rules for
    sensitivity."""
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    _null_the_status(db_session, tid)

    res = close(client, tid)

    assert res.status_code == 200, res.text
    assert res.json()["status"] == TicketStatus.CLOSED.value
    assert ticket(db_session, tid).status == TicketStatus.CLOSED.value
    assert db_session.get(models.Equipment, item.id).status == EquipmentStatus.FUNCTIONAL.value


def test_a_sibling_with_no_status_keeps_the_item_broken(client, db_session, mock_matrix_db):
    item = item_named(db_session, "SA100")
    first = open_ticket(client, item.id, "Cracked Housing")
    second = open_ticket(client, item.id, "Dead Battery")
    _null_the_status(db_session, second)

    assert close(client, first).status_code == 200

    db_session.expire_all()
    assert db_session.get(models.Equipment, item.id).status == EquipmentStatus.MALFUNCTIONING.value
    assert ticket(db_session, second).status is None


def test_the_item_route_closes_a_ticket_with_no_status_too(client, db_session, mock_matrix_db):
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    _null_the_status(db_session, tid)

    res = client.post(f"/maintenance/fix/{item.id}", headers=create_auth_header("u_tech_a"))

    assert res.status_code == 200, res.text
    assert ticket(db_session, tid).status == TicketStatus.CLOSED.value


# --- refusals --------------------------------------------------------------


def test_closing_a_closed_ticket_is_409_and_writes_nothing(client, db_session, mock_matrix_db):
    item = item_named(db_session, "SA100")
    first = open_ticket(client, item.id, "Cracked Housing")
    open_ticket(client, item.id, "Dead Battery")
    assert close(client, first).status_code == 200
    before = snapshot(db_session, item.id)

    # Someone else, so a second write of technician_id would show.
    res = close(client, first, who="u_master")

    assert res.status_code == 409, res.text
    assert res.json()["detail"] == "Ticket already closed"
    assert snapshot(db_session, item.id) == before


def test_an_unknown_and_an_unseen_ticket_answer_the_same_404(client, db_session, mock_matrix_db):
    """404 before 403: Company B's tech cannot see SA100, so its ticket must
    read exactly like an id that was never issued."""
    item = item_named(db_session, "SA100")
    unseen = open_ticket(client, item.id)
    before = snapshot(db_session, item.id)

    unknown = close(client, unseen + 1000, who="u_tech_b")
    hidden = close(client, unseen, who="u_tech_b")

    assert unknown.status_code == 404
    assert hidden.status_code == 404
    assert hidden.content == unknown.content
    assert hidden.json()["detail"] == "Ticket not found"
    assert snapshot(db_session, item.id) == before


def test_seeing_the_ticket_is_not_enough_to_close_it(client, db_session, mock_matrix_db):
    """RESOLVE_FAULT, not REPORT_STATUS, and no possession arm. Company A's
    commander holds REPORT_STATUS there and the soldier holds the item; both
    can see the ticket and neither may close it."""
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    before = snapshot(db_session, item.id)

    for who in ("u_cmdr_a", "u_soldier_a"):
        res = close(client, tid, who=who)
        assert res.status_code == 403, (who, res.text)

    assert snapshot(db_session, item.id) == before


def test_closing_needs_a_login(client, db_session, mock_matrix_db):
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)

    assert client.post(f"/maintenance/tickets/{tid}/close").status_code == 401


# --- the response ----------------------------------------------------------


def test_the_close_answers_with_the_closed_ticket(client, db_session, mock_matrix_db):
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id, "Dead Battery")
    tech = user_named(db_session, "u_tech_a")

    res = close(client, tid)

    body = res.json()
    listed = {
        t["id"]: t
        for t in client.get("/tickets/", headers=create_auth_header("u_tech_a")).json()
    }
    assert body == listed[tid]
    assert body["status"] == TicketStatus.CLOSED.value
    assert body["fault_type"] == "Dead Battery"
    assert body["closed_at"] is not None
    assert body["technician_id"] == tech.id


def test_the_ticket_list_carries_who_closed(client, db_session, mock_matrix_db):
    item = item_named(db_session, "SA100")
    first = open_ticket(client, item.id, "Cracked Housing")
    second = open_ticket(client, item.id, "Dead Battery")
    tech = user_named(db_session, "u_tech_a")
    close(client, first)

    listed = {
        t["id"]: t
        for t in client.get("/tickets/", headers=create_auth_header("u_master")).json()
    }

    assert listed[first]["technician_id"] == tech.id
    assert listed[second]["technician_id"] is None
    assert "technician_id" in listed[second]


# --- the item route keeps its meaning --------------------------------------


def test_the_item_route_still_closes_every_ticket_and_records_the_closer(
    client, db_session, mock_matrix_db
):
    """POST /maintenance/fix/{equipment_id} declares the whole item fixed, so
    closing all of its tickets is right there. It now records who did."""
    item = item_named(db_session, "SA100")
    first = open_ticket(client, item.id, "Cracked Housing")
    second = open_ticket(client, item.id, "Dead Battery")
    tech = user_named(db_session, "u_tech_a")

    res = client.post(f"/maintenance/fix/{item.id}", headers=create_auth_header("u_tech_a"))

    assert res.status_code == 200, res.text
    for tid in (first, second):
        t = ticket(db_session, tid)
        assert t.status == TicketStatus.CLOSED.value
        assert t.closed_at is not None
        assert t.technician_id == tech.id
    assert db_session.get(models.Equipment, item.id).status == EquipmentStatus.FUNCTIONAL.value


def test_the_item_route_leaves_a_closed_tickets_closer_alone(client, db_session, mock_matrix_db):
    """_close_tickets skips tickets already closed, so a later whole-item
    repair does not rewrite who closed an earlier one, or when."""
    item = item_named(db_session, "SA100")
    first = open_ticket(client, item.id, "Cracked Housing")
    open_ticket(client, item.id, "Dead Battery")
    tech = user_named(db_session, "u_tech_a")
    close(client, first)
    closed_at = ticket(db_session, first).closed_at

    client.post(f"/maintenance/fix/{item.id}", headers=create_auth_header("u_master"))

    t = ticket(db_session, first)
    assert t.technician_id == tech.id
    assert t.closed_at == closed_at
