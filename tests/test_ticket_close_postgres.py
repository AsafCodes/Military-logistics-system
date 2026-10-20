"""The routes that open and close an item's tickets act on the item as it stands
once they hold its row lock.

DATA-M21. close_ticket counts the item's open tickets after closing one and
repairs the item when none is left; report_fault and fix_equipment decide
their history row from the item's status. Each read could be stale: a close
counting a sibling another close is about to commit would leave the item
Malfunctioning with no ticket open, and a report reading "already
Malfunctioning" while a repair commits would leave it Functional with a ticket
open. So each takes the item's row lock (maintenance._lock_item,
SELECT ... FOR UPDATE, populate_existing) and reads after it. DATA-M22's
start_ticket and release_ticket take it too, so every writer of an item's
tickets runs one at a time; start_ticket's case is below with the others.

Each test holds that lock from a second connection, changes what the route is
about to read, and commits. The route must wait, then act on the committed
change. Waiting alone proves little for report_fault and fix_equipment: their
own writes lock the row too (the ticket INSERT's foreign-key check takes FOR
KEY SHARE on the item, the status UPDATE takes the row), so they wait with or
without _lock_item, measured. The outcome is what tells the two apart.

SQLite has no row locks, so only Postgres can show this. Skipped unless
TEST_POSTGRES_URL is set; CI sets it (.github/workflows/ci.yml). db_session is
overridden the way test_daily_movement_postgres.py does it, so mock_matrix_db
and client run unchanged against Postgres.
"""
import threading

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.database import Base
from backend.enums import ChangeReason, EquipmentStatus, TicketStatus
from tests.conftest import create_auth_header
from tests.test_utc_migration_postgres import POSTGRES_URL, pg_schema  # noqa: F401  (a fixture)

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="TEST_POSTGRES_URL is not set; see this module's docstring",
)

# Long enough for an unblocked request to have finished many times over (they
# take milliseconds here), short enough to keep the module quick.
STILL_WAITING_AFTER = 0.5
FINISHES_WITHIN = 15


@pytest.fixture
def db_session(pg_schema):  # noqa: F811  (overrides conftest's SQLite session)
    Base.metadata.create_all(bind=pg_schema)
    session = sessionmaker(autocommit=False, autoflush=False, bind=pg_schema)()
    try:
        yield session
    finally:
        session.close()


def _report(client, item_id, fault):
    res = client.post(
        "/maintenance/report",
        json={"equipment_id": item_id, "fault_name": fault, "description": "d"},
        headers=create_auth_header("u_soldier_a"),
    )
    assert res.status_code == 200, res.text
    return res.json()["ticket_id"]


def _status(db, item_id):
    db.expire_all()
    return db.get(models.Equipment, item_id).status


def _history(db, item_id, reason):
    db.expire_all()
    return (
        db.query(models.EquipmentStatusHistory)
        .filter_by(equipment_id=item_id, change_reason=reason.value)
        .all()
    )


def _close_the_sibling(db, item_id, first, second):
    """A close of the OTHER ticket commits while this one waits."""
    return (
        text("UPDATE maintenance_logs SET status = :closed WHERE id = :id"),
        {"closed": TicketStatus.CLOSED.value, "id": second},
    )


def _close_the_same_ticket(db, item_id, first, second):
    """A second close of THIS ticket commits while this one waits."""
    return (
        text("UPDATE maintenance_logs SET status = :closed WHERE id = :id"),
        {"closed": TicketStatus.CLOSED.value, "id": first},
    )


def _repair_the_item(db, item_id, first, second):
    """A repair commits while this request waits: the item is Functional again."""
    return (
        text("UPDATE equipment SET status = :ok WHERE id = :id"),
        {"ok": EquipmentStatus.FUNCTIONAL.value, "id": item_id},
    )


def _expect_repaired(db, item_id):
    # It counted the sibling as closed, so its ticket was the last one.
    assert _status(db, item_id) == EquipmentStatus.FUNCTIONAL.value
    assert len(_history(db, item_id, ChangeReason.REPAIR)) == 1


def _expect_left_alone(db, item_id):
    # The 409 wrote nothing: the sibling is still open, so the item stays
    # broken, and no REPAIR row claims otherwise.
    assert _status(db, item_id) == EquipmentStatus.MALFUNCTIONING.value
    assert _history(db, item_id, ChangeReason.REPAIR) == []


def _expect_broken_again(db, item_id):
    # It read Functional, so its fault is a transition with a history row.
    assert _status(db, item_id) == EquipmentStatus.MALFUNCTIONING.value
    reports = _history(db, item_id, ChangeReason.FAULT_REPORT)
    assert [(r.old_status, r.new_status) for r in reports][-1] == (
        EquipmentStatus.FUNCTIONAL.value, EquipmentStatus.MALFUNCTIONING.value,
    )


def _expect_no_second_repair(db, item_id):
    # It read Functional, so there was nothing to repair and no history row:
    # a stale read writes one claiming Malfunctioning -> Functional.
    assert _status(db, item_id) == EquipmentStatus.FUNCTIONAL.value
    assert _history(db, item_id, ChangeReason.REPAIR) == []


def _expect_not_revived(db, item_id):
    # DATA-M22. The start matched no Open ticket and wrote nothing: the ticket
    # closed meanwhile stays Closed and nobody's, and the sibling is untouched.
    db.expire_all()
    tickets = (
        db.query(models.MaintenanceLog)
        .filter_by(equipment_id=item_id)
        .order_by(models.MaintenanceLog.id)
        .all()
    )
    assert [(t.status, t.assigned_to_id, t.started_at) for t in tickets] == [
        (TicketStatus.CLOSED.value, None, None),
        (TicketStatus.OPEN.value, None, None),
    ]
    # DATA-M22-2. Nor a claim: the claim is written only once the ticket has
    # moved, so a start that waited and lost leaves no take behind.
    assert db.query(models.TicketClaim).count() == 0


def _close_first(client, item_id, first):
    return client.post(
        f"/maintenance/tickets/{first}/close", headers=create_auth_header("u_tech_a")
    )


def _start_first(client, item_id, first):
    return client.post(
        f"/maintenance/tickets/{first}/start", headers=create_auth_header("u_tech_a")
    )


# name -> (the request, what the lock holder commits meanwhile, the status
# the request must answer, what the database must then hold)
CASES = {
    "close_ticket, sibling closed meanwhile": (
        _close_first, _close_the_sibling, 200, _expect_repaired,
    ),
    # The status re-read after the lock is what answers this one; read before
    # it, the ticket is still Open and the close answers 200 having closed
    # nothing.
    "close_ticket, same ticket closed meanwhile": (
        _close_first, _close_the_same_ticket, 409, _expect_left_alone,
    ),
    # DATA-M22. Without the lock the start does not wait: it moves the ticket
    # before the close commits. The UPDATE's own `status = 'Open'` is what
    # answers 409 once it has waited.
    "start_ticket, same ticket closed meanwhile": (
        _start_first, _close_the_same_ticket, 409, _expect_not_revived,
    ),
    # The fault name is in the catalog by now, so the request reaches the lock
    # without first committing a new fault type.
    "report_fault, item repaired meanwhile": (
        lambda client, item_id, first: client.post(
            "/maintenance/report",
            json={"equipment_id": item_id, "fault_name": "Cracked Housing", "description": "d"},
            headers=create_auth_header("u_soldier_a"),
        ),
        _repair_the_item,
        200,
        _expect_broken_again,
    ),
    "fix_equipment, item repaired meanwhile": (
        lambda client, item_id, first: client.post(
            f"/maintenance/fix/{item_id}", headers=create_auth_header("u_tech_a")
        ),
        _repair_the_item,
        200,
        _expect_no_second_repair,
    ),
}


@pytest.mark.parametrize("route", sorted(CASES))
def test_the_route_reads_the_item_after_its_row_lock(
    route, client, db_session, mock_matrix_db, pg_schema  # noqa: F811
):
    send_request, change, answer, expect = CASES[route]
    item_id = db_session.query(models.Equipment.id).filter_by(serial_number="SA100").scalar()
    first = _report(client, item_id, "Cracked Housing")
    second = _report(client, item_id, "Dead Battery")
    assert _status(db_session, item_id) == EquipmentStatus.MALFUNCTIONING.value
    # The request below runs on db_session in another thread; end this
    # thread's use of it first.
    db_session.rollback()

    result = {}

    def send():
        try:
            result["response"] = send_request(client, item_id, first)
        except BaseException as exc:  # noqa: BLE001  (reported by the main thread's assert)
            result["error"] = exc

    locker = pg_schema.connect()
    worker = threading.Thread(target=send, daemon=True)
    released = False
    try:
        locker.begin()
        locker.execute(
            text("SELECT id FROM equipment WHERE id = :id FOR UPDATE"), {"id": item_id}
        )

        worker.start()
        worker.join(STILL_WAITING_AFTER)
        assert worker.is_alive(), (
            f"{route} finished while another transaction held the item's row lock: "
            f"{result}"
        )

        locker.execute(*change(db_session, item_id, first, second))
        locker.commit()
        released = True
    finally:
        if not released:
            locker.rollback()
        locker.close()

    worker.join(FINISHES_WITHIN)
    assert not worker.is_alive(), f"{route} still waiting after the lock was released"
    assert "error" not in result, result.get("error")
    assert result["response"].status_code == answer, result["response"].text
    expect(db_session, item_id)
