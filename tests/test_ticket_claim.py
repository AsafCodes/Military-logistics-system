"""DATA-M22: a ticket can be taken, and the In Progress tab means something.

POST /maintenance/tickets/{ticket_id}/start moves an Open ticket to In
Progress and records who took it and when; .../release puts it back to Open
and clears both. Both ask RESOLVE_FAULT over the item's group, the verb that
closes. Neither touches the item, so neither writes a history row or an event.
A close keeps the assignee: the ticket then says who worked it and who closed
it.

Tickets are opened through POST /maintenance/report, as in
test_ticket_close.py, so each one starts from what production would hold.
"""
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from backend import clock, models
from backend.enums import EquipmentStatus, Sensitivity, TicketStatus
from tests.conftest import create_auth_header
from tests.test_group_schema import _downgrade, _schema_snapshot, _upgrade
from tests.test_ticket_close import (
    close,
    events,
    history_rows,
    item_named,
    open_ticket,
    ticket,
    user_named,
)


def start(client, ticket_id, who="u_tech_a"):
    return client.post(
        f"/maintenance/tickets/{ticket_id}/start", headers=create_auth_header(who)
    )


def release(client, ticket_id, who="u_tech_a"):
    return client.post(
        f"/maintenance/tickets/{ticket_id}/release", headers=create_auth_header(who)
    )


ROUTES = {"start": start, "release": release}


def snapshot(db, item_id):
    """Everything start or release may write, and everything they must not."""
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
        [
            (t.id, t.status, t.closed_at, t.technician_id, t.assigned_to_id, t.started_at)
            for t in tickets
        ],
        len(history_rows(db, item_id)),
        len(events(db, item_id)),
    )


def _set_status(db, ticket_id, status):
    """Raw SQL, for the states no route reaches: Waiting for Parts (no writer)
    and NULL (the column is nullable, DATA-M12)."""
    db.execute(
        text("UPDATE maintenance_logs SET status = :s WHERE id = :id"),
        {"s": status, "id": ticket_id},
    )
    db.commit()


# --- start and release -----------------------------------------------------


def test_start_takes_the_ticket_and_writes_nothing_about_the_item(
    client, db_session, mock_matrix_db
):
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    tech = user_named(db_session, "u_tech_a")
    history_before = len(history_rows(db_session, item.id))
    events_before = len(events(db_session, item.id))
    before = clock.utcnow()

    res = start(client, tid)

    assert res.status_code == 200, res.text
    t = ticket(db_session, tid)
    assert t.status == TicketStatus.IN_PROGRESS.value
    assert t.assigned_to_id == tech.id
    assert before <= t.started_at <= clock.utcnow()
    assert t.closed_at is None and t.technician_id is None

    # The item is as broken as it was, so neither audit table has anything to say.
    assert db_session.get(models.Equipment, item.id).status == EquipmentStatus.MALFUNCTIONING.value
    assert len(history_rows(db_session, item.id)) == history_before
    assert len(events(db_session, item.id)) == events_before


def test_release_puts_the_ticket_back_and_clears_who_took_it(client, db_session, mock_matrix_db):
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    assert start(client, tid).status_code == 200
    history_before = len(history_rows(db_session, item.id))
    events_before = len(events(db_session, item.id))

    res = release(client, tid)

    assert res.status_code == 200, res.text
    t = ticket(db_session, tid)
    assert t.status == TicketStatus.OPEN.value
    assert t.assigned_to_id is None
    assert t.started_at is None
    assert db_session.get(models.Equipment, item.id).status == EquipmentStatus.MALFUNCTIONING.value
    assert len(history_rows(db_session, item.id)) == history_before
    assert len(events(db_session, item.id)) == events_before


def test_anyone_who_may_resolve_it_may_release_it(client, db_session, mock_matrix_db):
    """Not only whoever took it: a ticket someone walked away from can be freed."""
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    assert start(client, tid, who="u_tech_a").status_code == 200

    res = release(client, tid, who="u_master")

    assert res.status_code == 200, res.text
    assert ticket(db_session, tid).status == TicketStatus.OPEN.value


def test_a_released_ticket_can_be_taken_again_by_someone_else(client, db_session, mock_matrix_db):
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    assert start(client, tid, who="u_tech_a").status_code == 200
    first_start = ticket(db_session, tid).started_at
    assert release(client, tid).status_code == 200

    res = start(client, tid, who="u_master")

    assert res.status_code == 200, res.text
    t = ticket(db_session, tid)
    assert t.assigned_to_id == user_named(db_session, "u_master").id
    assert t.started_at >= first_start


# --- refusals --------------------------------------------------------------


def _to_in_progress(client, db, tid):
    assert start(client, tid).status_code == 200


def _to_closed(client, db, tid):
    assert close(client, tid).status_code == 200


def _to_waiting(client, db, tid):
    _set_status(db, tid, TicketStatus.WAITING_PARTS.value)


def _to_null(client, db, tid):
    _set_status(db, tid, None)


def _left_open(client, db, tid):
    pass


def _to_taken_then_closed(client, db, tid):
    """Closed with its assignee kept, so a stray clear of it would show."""
    assert start(client, tid).status_code == 200
    assert close(client, tid).status_code == 200


# (route, how the ticket got where it is, the 409's detail)
CONFLICTS = {
    "start an In Progress ticket": ("start", _to_in_progress, "Ticket is not open"),
    "start a Closed ticket": ("start", _to_closed, "Ticket is not open"),
    "start a Waiting ticket": ("start", _to_waiting, "Ticket is not open"),
    "start a ticket with no status": ("start", _to_null, "Ticket is not open"),
    "release an Open ticket": ("release", _left_open, "Ticket is not in progress"),
    "release a Closed ticket": ("release", _to_closed, "Ticket is not in progress"),
    "release a ticket taken and then closed": (
        "release", _to_taken_then_closed, "Ticket is not in progress",
    ),
    "start a ticket taken and then closed": ("start", _to_taken_then_closed, "Ticket is not open"),
    "release a Waiting ticket": ("release", _to_waiting, "Ticket is not in progress"),
    "release a ticket with no status": ("release", _to_null, "Ticket is not in progress"),
}


@pytest.mark.parametrize("case", sorted(CONFLICTS))
def test_a_ticket_not_in_the_state_the_route_moves_from_is_409_and_writes_nothing(
    case, client, db_session, mock_matrix_db
):
    route, arrange, detail = CONFLICTS[case]
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id, "Cracked Housing")
    # A sibling, so closing tid leaves the item broken and the snapshot
    # compares like with like.
    open_ticket(client, item.id, "Dead Battery")
    arrange(client, db_session, tid)
    before = snapshot(db_session, item.id)

    # Someone other than whoever started it, so a stray write of the
    # assignee would show.
    res = ROUTES[route](client, tid, who="u_master")

    assert res.status_code == 409, res.text
    assert res.json()["detail"] == detail
    assert snapshot(db_session, item.id) == before


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_an_unknown_and_an_unseen_ticket_answer_the_same_404(
    route, client, db_session, mock_matrix_db
):
    """Company B's tech cannot see SA100, so its ticket must read exactly like
    an id that was never issued."""
    item = item_named(db_session, "SA100")
    unseen = open_ticket(client, item.id)
    if route == "release":
        assert start(client, unseen).status_code == 200
    before = snapshot(db_session, item.id)

    unknown = ROUTES[route](client, unseen + 1000, who="u_tech_b")
    hidden = ROUTES[route](client, unseen, who="u_tech_b")

    assert unknown.status_code == 404
    assert hidden.status_code == 404
    assert hidden.content == unknown.content
    assert hidden.json()["detail"] == "Ticket not found"
    assert snapshot(db_session, item.id) == before


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_seeing_the_ticket_is_not_enough_to_take_or_release_it(
    route, client, db_session, mock_matrix_db
):
    """RESOLVE_FAULT, and no possession arm. Company A's commander holds
    REPORT_STATUS there and the soldier holds the item; both can see the
    ticket and neither may take or release it."""
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    if route == "release":
        assert start(client, tid).status_code == 200
    before = snapshot(db_session, item.id)

    for who in ("u_cmdr_a", "u_soldier_a"):
        res = ROUTES[route](client, tid, who=who)
        assert res.status_code == 403, (who, res.text)

    assert snapshot(db_session, item.id) == before


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_a_classified_item_s_ticket_is_404_to_a_resolver_without_clearance(
    route, client, db_session, mock_matrix_db
):
    """u_tech_a holds RESOLVE_FAULT over SA100's company and not
    VIEW_CLASSIFIED, so a classified item's ticket reads exactly like one that
    was never issued (DATA-H3-3's rule, through scope_equipment_derived_query).
    u_master holds both and gets through."""
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    if route == "release":
        assert start(client, tid).status_code == 200
    item = db_session.get(models.Equipment, item.id)
    item.sensitivity = Sensitivity.CLASSIFIED.value
    db_session.commit()
    before = snapshot(db_session, item.id)

    hidden = ROUTES[route](client, tid, who="u_tech_a")
    unknown = ROUTES[route](client, tid + 1000, who="u_tech_a")

    assert hidden.status_code == 404, hidden.text
    assert hidden.content == unknown.content
    assert snapshot(db_session, item.id) == before
    assert ROUTES[route](client, tid, who="u_master").status_code == 200


def test_a_resolver_over_an_ancestor_group_may_take_it(client, db_session, mock_matrix_db):
    """u_brig_tech's RESOLVE_FAULT is over the brigade, the item's company's
    ancestor: the grant reaches down."""
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)

    res = start(client, tid, who="u_brig_tech")

    assert res.status_code == 200, res.text
    assert ticket(db_session, tid).assigned_to_id == user_named(db_session, "u_brig_tech").id


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_an_inactive_account_may_not_take_or_release(route, client, db_session, mock_matrix_db):
    """get_current_active_user's refusal, before anything is resolved."""
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    if route == "release":
        assert start(client, tid).status_code == 200
    user_named(db_session, "u_master").is_active_duty = False
    db_session.commit()
    before = snapshot(db_session, item.id)

    res = ROUTES[route](client, tid, who="u_master")

    assert res.status_code == 400, res.text
    assert res.json()["detail"] == "Inactive user"
    assert snapshot(db_session, item.id) == before


def test_an_assigned_user_cannot_be_deleted_out_from_under_the_ticket(
    client, db_session, mock_matrix_db
):
    """assigned_to_id is RESTRICT, like every key in models.py: deleting the
    account a ticket names is refused rather than leaving the ticket pointing
    at nobody."""
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    assert start(client, tid, who="u_bat_tech").status_code == 200
    bat_tech = user_named(db_session, "u_bat_tech").id
    # Everything else that names this account, so the only thing left to
    # refuse the delete is the ticket.
    for table in ("group_memberships", "grants"):
        db_session.execute(text(f"DELETE FROM {table} WHERE user_id = :u"), {"u": bat_tech})
    db_session.commit()

    with pytest.raises(IntegrityError):
        db_session.execute(text("DELETE FROM users WHERE id = :u"), {"u": bat_tech})
        db_session.commit()
    db_session.rollback()

    db_session.execute(
        text("UPDATE maintenance_logs SET assigned_to_id = NULL WHERE id = :t"), {"t": tid}
    )
    db_session.execute(text("DELETE FROM users WHERE id = :u"), {"u": bat_tech})
    db_session.commit()


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_taking_or_releasing_needs_a_login(route, client, db_session, mock_matrix_db):
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)

    assert client.post(f"/maintenance/tickets/{tid}/{route}").status_code == 401


# --- closing a ticket someone took -----------------------------------------


def test_closing_a_taken_ticket_keeps_who_took_it_and_records_who_closed(
    client, db_session, mock_matrix_db
):
    """The last ticket, so the close also repairs the item."""
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    assert start(client, tid, who="u_tech_a").status_code == 200
    started_at = ticket(db_session, tid).started_at

    res = close(client, tid, who="u_master")

    assert res.status_code == 200, res.text
    t = ticket(db_session, tid)
    assert t.status == TicketStatus.CLOSED.value
    assert t.assigned_to_id == user_named(db_session, "u_tech_a").id
    assert t.started_at == started_at
    assert t.technician_id == user_named(db_session, "u_master").id
    assert db_session.get(models.Equipment, item.id).status == EquipmentStatus.FUNCTIONAL.value


def test_a_taken_sibling_keeps_the_item_broken(client, db_session, mock_matrix_db):
    """In Progress is not closed: the fault is still there."""
    item = item_named(db_session, "SA100")
    first = open_ticket(client, item.id, "Cracked Housing")
    second = open_ticket(client, item.id, "Dead Battery")
    assert start(client, second).status_code == 200

    assert close(client, first).status_code == 200

    db_session.expire_all()
    assert db_session.get(models.Equipment, item.id).status == EquipmentStatus.MALFUNCTIONING.value
    assert ticket(db_session, second).status == TicketStatus.IN_PROGRESS.value


def test_the_item_route_closes_a_taken_ticket_and_keeps_who_took_it(
    client, db_session, mock_matrix_db
):
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    assert start(client, tid, who="u_tech_a").status_code == 200

    res = client.post(f"/maintenance/fix/{item.id}", headers=create_auth_header("u_master"))

    assert res.status_code == 200, res.text
    t = ticket(db_session, tid)
    assert t.status == TicketStatus.CLOSED.value
    assert t.assigned_to_id == user_named(db_session, "u_tech_a").id


# --- the list and the response ---------------------------------------------


def test_the_in_progress_filter_finds_a_taken_ticket(client, db_session, mock_matrix_db):
    item = item_named(db_session, "SA100")
    taken = open_ticket(client, item.id, "Cracked Housing")
    waiting = open_ticket(client, item.id, "Dead Battery")
    assert start(client, taken).status_code == 200

    def ids(status):
        res = client.get(
            "/tickets/", params={"status_filter": status.value}, headers=create_auth_header("u_master")
        )
        assert res.status_code == 200, res.text
        return [t["id"] for t in res.json()]

    assert ids(TicketStatus.IN_PROGRESS) == [taken]
    assert ids(TicketStatus.OPEN) == [waiting]


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_the_route_answers_with_the_ticket_as_the_list_shows_it(
    route, client, db_session, mock_matrix_db
):
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id, "Dead Battery")
    if route == "release":
        assert start(client, tid).status_code == 200

    res = ROUTES[route](client, tid)

    assert res.status_code == 200, res.text
    listed = {
        t["id"]: t
        for t in client.get("/tickets/", headers=create_auth_header("u_tech_a")).json()
    }
    assert res.json() == listed[tid]


def test_the_list_carries_who_took_each_ticket_and_when(client, db_session, mock_matrix_db):
    item = item_named(db_session, "SA100")
    taken = open_ticket(client, item.id, "Cracked Housing")
    untouched = open_ticket(client, item.id, "Dead Battery")
    assert start(client, taken).status_code == 200
    tech = user_named(db_session, "u_tech_a")

    listed = {
        t["id"]: t
        for t in client.get("/tickets/", headers=create_auth_header("u_master")).json()
    }

    assert listed[taken]["status"] == TicketStatus.IN_PROGRESS.value
    assert listed[taken]["assigned_to_id"] == tech.id
    assert listed[taken]["assigned_to_name"] == tech.full_name
    assert listed[taken]["started_at"] is not None
    for field in ("assigned_to_id", "assigned_to_name", "started_at"):
        assert field in listed[untouched] and listed[untouched][field] is None, field


def test_an_assignee_with_no_name_does_not_break_the_list(client, db_session, mock_matrix_db):
    """users.full_name is nullable (DATA-L11), so assigned_to_name is Optional."""
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    assert start(client, tid).status_code == 200
    db_session.execute(
        text("UPDATE users SET full_name = NULL WHERE personal_number = 'u_tech_a'")
    )
    db_session.commit()

    res = client.get("/tickets/", headers=create_auth_header("u_master"))

    assert res.status_code == 200, res.text
    listed = {t["id"]: t for t in res.json()}
    assert listed[tid]["assigned_to_name"] is None
    assert listed[tid]["assigned_to_id"] == user_named(db_session, "u_tech_a").id


# --- the revision ----------------------------------------------------------

REVISION, PARENT = "7a2f5c9d1e38", "3c6a0f8e2d71"


def test_the_revision_adds_both_columns_the_key_and_the_index_to_existing_rows(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm22.db'}")
    _upgrade(engine, PARENT)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO maintenance_logs (id, description, status) VALUES (1, 'old', 'Open')"
        )

    _upgrade(engine, REVISION)

    insp = inspect(engine)
    columns = {c["name"]: c for c in insp.get_columns("maintenance_logs")}
    keys = {fk["name"]: fk for fk in insp.get_foreign_keys("maintenance_logs")}
    indexes = {ix["name"]: ix for ix in insp.get_indexes("maintenance_logs")}
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT assigned_to_id, started_at FROM maintenance_logs WHERE id = 1")
        ).one()
    engine.dispose()

    assert columns["assigned_to_id"]["nullable"] and columns["started_at"]["nullable"]
    key = keys["fk_maintenance_logs_assigned_to_id"]
    assert (key["referred_table"], key["constrained_columns"], key["options"].get("ondelete")) == (
        "users", ["assigned_to_id"], "RESTRICT"
    )
    assert indexes["ix_maintenance_logs_assigned_to_id"]["column_names"] == ["assigned_to_id"]
    assert tuple(row) == (None, None)


def test_the_downgrade_lands_on_the_parent_s_schema(tmp_path):
    reference = create_engine(f"sqlite:///{tmp_path / 'parent.db'}")
    _upgrade(reference, PARENT)
    at_parent = _schema_snapshot(reference)
    reference.dispose()

    engine = create_engine(f"sqlite:///{tmp_path / 'roundtrip.db'}")
    _upgrade(engine, REVISION)
    at_revision = _schema_snapshot(engine)
    _downgrade(engine, PARENT)
    downgraded = _schema_snapshot(engine)
    _upgrade(engine, REVISION)
    upgraded_again = _schema_snapshot(engine)
    engine.dispose()

    assert at_revision != at_parent
    assert downgraded == at_parent
    assert upgraded_again == at_revision
