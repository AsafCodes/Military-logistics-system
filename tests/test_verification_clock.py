"""DATA-H5: only a physical verification may advance last_verified_at.

assign_owner and transfer_equipment both reset the clock, and the model
defaulted it to creation time, so paperwork made equipment read compliant. The
AST guard in test_audit_trail.py keeps the assignment inside
audit_trail.set_last_verified_at; these tests pin the behaviour through the API,
where the forgery was actually visible.
"""
from datetime import timedelta

import pytest
from sqlalchemy import text

from backend import audit_trail, clock, models
from backend.enums import EventType
from tests.conftest import create_auth_header
from tests.test_audit_trail import item_named, logs_for

STALE = "2020-01-01 00:00:00"
NEVER_REPORTED = "מעולם לא דווח"


def set_clock(db_session, serial, value):
    item = item_named(db_session, serial)
    db_session.execute(
        text("UPDATE equipment SET last_verified_at = :v WHERE id = :id"),
        {"v": value, "id": item.id},
    )
    db_session.commit()
    db_session.expire_all()
    return item_named(db_session, serial)


def fresh_clock(db_session, serial):
    db_session.expire_all()
    return item_named(db_session, serial).last_verified_at


def listed(client, who, serial):
    res = client.get("/equipment/accessible", headers=create_auth_header(who))
    assert res.status_code == 200, res.text
    rows = [r for r in res.json() if r["serial_number"] == serial]
    assert len(rows) == 1, rows
    return rows[0]


def condition_report(item, reported_status="Functional", findings=None):
    return {
        "equipment_id": item.id,
        "verification_type": "daily",
        "reported_status": reported_status,
        "findings": findings,
        "action_required": False,
    }


# --- 1. Paperwork does not advance the clock ---------------------------------

PAPERWORK = [
    ("/equipment/assign_owner/", {"owner_id": "TECH"}),
    ("/equipment/transfer", {"to_holder_id": "TECH"}),
    ("/equipment/transfer", {"to_location": "Armory"}),
]
PAPERWORK_IDS = ["assign_owner", "transfer_person", "transfer_location"]


def paperwork_payload(item, body, mock_matrix_db):
    payload = {"equipment_id": item.id}
    payload.update(
        {k: (mock_matrix_db["company_tech_a"].id if v == "TECH" else v) for k, v in body.items()}
    )
    return payload


@pytest.mark.parametrize("path,body", PAPERWORK, ids=PAPERWORK_IDS)
@pytest.mark.parametrize("start", [STALE, None], ids=["stale", "never_verified"])
def test_paperwork_leaves_the_clock_exactly_where_it_was(
    client, db_session, mock_matrix_db, path, body, start
):
    """The ticket's headline, at every route it names, from both starting states.

    NULL is its own case: a route that did `if item.last_verified_at: ...` would
    leave stale items alone and still stamp never-verified ones.
    """
    item = set_clock(db_session, "SA100", start)
    before = item.last_verified_at

    res = client.post(
        path,
        json=paperwork_payload(item, body, mock_matrix_db),
        headers=create_auth_header("u_cmdr_a"),
    )
    assert res.status_code == 200, res.text

    assert fresh_clock(db_session, "SA100") == before, (
        f"{path} advanced last_verified_at -- compliance forged by paperwork"
    )
    row = listed(client, "u_cmdr_a", "SA100")
    assert row["compliance_level"] == "SEVERE", row


def test_a_bulk_transfer_does_not_make_the_fleet_compliant(
    client, db_session, mock_matrix_db
):
    """The ticket's worst case: move everything, and nothing turns green."""
    serials = ["SA100", "TA300"]
    for serial in serials:
        item = set_clock(db_session, serial, STALE)
        res = client.post(
            "/equipment/transfer",
            json={"equipment_id": item.id, "to_location": "Armory"},
            headers=create_auth_header("u_cmdr_a"),
        )
        assert res.status_code == 200, res.text

    for serial in serials:
        assert listed(client, "u_cmdr_a", serial)["compliance_level"] == "SEVERE", serial


def test_passing_an_item_back_and_forth_never_refreshes_it(
    client, db_session, mock_matrix_db
):
    """Repeated handovers are still paperwork, however many there are."""
    item = set_clock(db_session, "SA100", STALE)
    before = item.last_verified_at
    tech = mock_matrix_db["company_tech_a"].id
    soldier = mock_matrix_db["soldier_a"].id

    for holder in (tech, soldier, tech, soldier):
        res = client.post(
            "/equipment/transfer",
            json={"equipment_id": item.id, "to_holder_id": holder},
            headers=create_auth_header("u_cmdr_a"),
        )
        assert res.status_code == 200, res.text

    assert fresh_clock(db_session, "SA100") == before


def test_reassigning_to_the_current_owner_does_not_refresh_it(
    client, db_session, mock_matrix_db
):
    """A no-op assignment is the cheapest possible forgery, so it gets its own case."""
    item = set_clock(db_session, "SA100", STALE)
    before = item.last_verified_at

    res = client.post(
        "/equipment/assign_owner/",
        json={"equipment_id": item.id, "owner_id": item.owner_user_id},
        headers=create_auth_header("u_cmdr_a"),
    )
    assert res.status_code == 200, res.text

    assert fresh_clock(db_session, "SA100") == before


# --- 2. New equipment starts unverified --------------------------------------


def test_a_created_item_starts_never_reported_everywhere_it_is_read(
    client, db_session, mock_matrix_db
):
    """Stored NULL, and every reader renders that honestly.

    The client also sends last_verified_at in the body. EquipmentCreate does not
    declare it, so a mass-assignment attempt to create an item pre-verified must
    be ignored rather than applied.
    """
    res = client.post(
        "/equipment/",
        json={
            "catalog_name": "Rifle",
            "serial_number": "H5-NEW",
            "last_verified_at": "2099-01-01T00:00:00Z",
        },
        headers=create_auth_header("u_master"),
    )
    assert res.status_code == 200, res.text

    assert fresh_clock(db_session, "H5-NEW") is None, (
        "a brand-new item carries a verification time nobody recorded"
    )

    row = listed(client, "u_master", "H5-NEW")
    assert row["compliance_level"] == "SEVERE", row
    assert row["compliance_check"] == NEVER_REPORTED, row

    report = client.get("/reports/query", headers=create_auth_header("u_master"))
    assert report.status_code == 200, report.text
    [rep] = [r for r in report.json() if r["serial_number"] == "H5-NEW"]
    assert rep["last_verified_at"] is None, rep
    assert rep["reporting_status"] != "Reported", rep


def test_a_row_built_without_a_clock_is_null(db_session, mock_matrix_db):
    """The path seed_data.py takes: no route, no kwarg, so only the model decides."""
    template = item_named(db_session, "SA100")
    item = models.Equipment(
        catalog_item_id=template.catalog_item_id,
        group_id=template.group_id,
        serial_number="H5-SEED",
    )
    db_session.add(item)
    db_session.commit()

    assert fresh_clock(db_session, "H5-SEED") is None
    assert item_named(db_session, "H5-SEED").compliance_level == "SEVERE"
    assert item_named(db_session, "H5-SEED").report_status == NEVER_REPORTED


# --- 3. Real verifications still advance it ----------------------------------


@pytest.mark.parametrize("start", [STALE, None], ids=["stale", "never_verified"])
def test_the_daily_verification_advances_the_clock_with_its_evidence(
    client, db_session, mock_matrix_db, start
):
    item = set_clock(db_session, "SA100", start)

    res = client.post(f"/equipment/{item.id}/verify", headers=create_auth_header("u_soldier_a"))
    assert res.status_code == 200, res.text
    assert res.json()["compliance"] == "GOOD"

    stamped = fresh_clock(db_session, "SA100")
    assert stamped is not None and clock.utcnow() - stamped < timedelta(minutes=1)

    [log] = logs_for(db_session, item, EventType.VERIFICATION)
    assert abs(log.timestamp - stamped) < timedelta(seconds=5), (
        "the clock and the row that proves it were written apart"
    )
    assert listed(client, "u_cmdr_a", "SA100")["compliance_level"] == "GOOD"


def test_a_condition_report_advances_a_never_verified_clock(
    client, db_session, mock_matrix_db
):
    """The stale case is test_audit_trail's clock-stall test; NULL is the new one."""
    item = set_clock(db_session, "SA100", None)

    res = client.post(
        "/verifications/", json=condition_report(item), headers=create_auth_header("u_cmdr_a")
    )
    assert res.status_code == 200, res.text

    stamped = fresh_clock(db_session, "SA100")
    assert stamped is not None and clock.utcnow() - stamped < timedelta(minutes=1)
    assert len(logs_for(db_session, item, EventType.CONDITION_REPORT)) == 1


@pytest.mark.parametrize(
    "who,expected",
    [("u_cmdr_a", 403), ("u_soldier_b", 404)],
    ids=["in_scope_not_holder", "out_of_scope"],
)
def test_a_refused_daily_verification_advances_nothing(
    client, db_session, mock_matrix_db, who, expected
):
    item = set_clock(db_session, "SA100", STALE)
    before = item.last_verified_at

    res = client.post(f"/equipment/{item.id}/verify", headers=create_auth_header(who))
    assert res.status_code == expected, res.text

    assert fresh_clock(db_session, "SA100") == before
    assert logs_for(db_session, item, EventType.VERIFICATION) == []


def test_a_condition_report_that_is_refused_midway_never_commits_the_clock(
    client, db_session, mock_matrix_db, monkeypatch
):
    """The clock now advances BEFORE set_status, which can still refuse with 409.

    The stamp used to be the last write before commit; now it is pending when
    set_status raises. Production is safe only because nothing commits and
    get_db's close discards it -- asserted by counting commits, since this
    harness shares one connection and cannot tell flushed from committed rows.
    """
    item = set_clock(db_session, "SA100", STALE)
    db_session.execute(text("UPDATE equipment SET status = NULL WHERE id = :id"), {"id": item.id})
    db_session.commit()

    commits = []
    real_commit = db_session.commit
    monkeypatch.setattr(db_session, "commit", lambda: (commits.append(1), real_commit())[1])

    res = client.post(
        "/verifications/",
        json=condition_report(item, "Malfunctioning", "x"),
        headers=create_auth_header("u_cmdr_a"),
    )
    assert res.status_code == 409, res.text
    assert commits == [], "the route committed a clock advance before refusing"

    db_session.rollback()
    assert fresh_clock(db_session, "SA100").year == 2020


# --- 4. The writer itself ----------------------------------------------------


def test_only_physical_verifications_are_verifying_events():
    """Widening this set is the defect, so widening it has to fail a test."""
    assert audit_trail.VERIFYING_EVENTS == {EventType.VERIFICATION, EventType.CONDITION_REPORT}


@pytest.mark.parametrize(
    "event_type",
    [e for e in EventType if e not in audit_trail.VERIFYING_EVENTS],
    ids=lambda e: e.name,
)
def test_the_writer_refuses_every_paperwork_event(db_session, mock_matrix_db, event_type):
    """Derived from EventType, so an event added next month is refused by default."""
    item = set_clock(db_session, "SA100", STALE)
    before = item.last_verified_at

    with pytest.raises(ValueError):
        audit_trail.set_last_verified_at(
            db_session, equipment=item, actor=mock_matrix_db["master"], event_type=event_type
        )

    assert item.last_verified_at == before, "refused, but the column moved anyway"
    assert not db_session.new, "refused, but an audit row was added anyway"
