"""DATA-H1-1: every stored and emitted timestamp is aware UTC, on both backends.

The defect this closes: every DateTime column was timezone-naive, so a naive
ISO string went out on the wire with no zone designator, and the browser's
`new Date(s)` parsed it as LOCAL time -- every displayed timestamp (and every
derived "time ago" delay) was wrong by the viewer's offset.

The fix (backend/clock.py) is a TypeDecorator, not a `timezone=True` flag,
because SQLAlchemy's SQLite dialect ignores that flag entirely -- it strips
tzinfo on write without converting and always reads back naive. Since dev and
this whole suite run on SQLite, the decorator is what makes both backends
agree in Python: aware on read, regardless of what is actually stored.

The decorator normalises ONLY on a DB round trip (see clock.py's docstring),
so a single call site left on `datetime.utcnow()` is a latent runtime
TypeError, not a silently wrong value -- which is why every call site had to
move in the same commit that introduced the decorator. test_no_bare_utcnow_
survives_in_backend below is the only mechanical proof that sweep was
complete; every other test here is a behavioural pin on top of it.
"""
import ast
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

import backend
from backend import clock, models
from tests.conftest import create_auth_header


# --- 1. The grep guard -------------------------------------------------------


def test_no_bare_utcnow_survives_in_backend():
    """No `datetime.utcnow()` anywhere under backend/.

    Parsed via ast rather than a text search, so a call spelled
    `xyz.utcnow()` on an unrelated object cannot produce a false pass, and a
    comment mentioning the string (as this file's own docstrings do) cannot
    produce a false failure.

    This is the ONLY defence against partial application: the decorator
    itself gives no protection against a missed call site (see clock.py's
    docstring), so completeness has to be proven mechanically, not by review.
    """
    backend_root = Path(backend.__file__).parent
    offenders = []

    for path in backend_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "utcnow"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "datetime"
            ):
                offenders.append(f"{path.relative_to(backend_root.parent)}:{node.lineno}")

    assert offenders == [], f"bare datetime.utcnow() survives at: {offenders}"


# --- 2 & 3. The decorator itself, against the raw DB -------------------------


def test_round_trip_is_aware_and_storage_format_is_unchanged(db_session):
    """Insert via the column default, commit, expunge, re-query.

    Two claims: the value Python sees is aware UTC (the fix), and the raw text
    SQLite actually stored is naive with no offset (H1-1 changes NOTHING about
    storage -- only H1-2 does, and only on Postgres).
    """
    # TransactionLog.timestamp carries the same UtcDateTime column type as
    # every other converted column and has no NOT NULL FKs to satisfy, so it
    # is the simplest fixture for a pure round-trip check.
    log = models.TransactionLog(event_type="TEST")
    db_session.add(log)
    db_session.commit()
    log_id = log.id

    db_session.expunge_all()

    reloaded = db_session.query(models.TransactionLog).filter_by(id=log_id).one()
    assert reloaded.timestamp.tzinfo is not None
    assert reloaded.timestamp.utcoffset() == timedelta(0)

    raw = db_session.execute(
        text("SELECT timestamp FROM transaction_logs WHERE id = :id"), {"id": log_id}
    ).scalar_one()
    assert "+" not in raw and "Z" not in raw, f"storage format changed: {raw!r}"


def test_naive_legacy_rows_read_back_aware(db_session):
    """A row written before this decorator existed -- naive text, no offset --
    must still come back aware and represent the same instant.

    This is the backward-compatibility claim: existing production rows are
    naive-UTC text, and the decorator must keep interpreting them as UTC
    rather than, say, refusing them or treating them as local time.
    """
    log = models.TransactionLog(event_type="LEGACY")
    db_session.add(log)
    db_session.commit()
    log_id = log.id

    naive_value = "2020-06-15 12:30:00.000000"
    db_session.execute(
        text("UPDATE transaction_logs SET timestamp = :ts WHERE id = :id"),
        {"ts": naive_value, "id": log_id},
    )
    db_session.commit()
    db_session.expunge_all()

    reloaded = db_session.query(models.TransactionLog).filter_by(id=log_id).one()
    assert reloaded.timestamp == datetime(2020, 6, 15, 12, 30, 0, tzinfo=timezone.utc)


def test_the_dialect_split_is_wired_the_way_postgres_needs(db_session):
    """DATA-H1-2's column type and bind parameter, checked WITHOUT a Postgres.

    tests/test_utc_migration_postgres.py proves all of this against a real
    server, but it skips whenever TEST_POSTGRES_URL is unset -- which is every
    local run and every developer machine without Docker. This test needs no
    server at all: a dialect object is enough to ask the decorator what it
    would do, so the repository keeps a mechanical guard on the split even when
    the Postgres suite is not running.

    The two halves are only correct together (see clock.UtcDateTime's
    docstring): a TIMESTAMPTZ column fed a naive value, or a naive column fed
    an aware one, both shift every timestamp by the server's offset. So both
    are asserted here, against both dialects, in one place.
    """
    from sqlalchemy.dialects import postgresql, sqlite

    pg, lite = postgresql.dialect(), sqlite.dialect()
    column = clock.UtcDateTime()
    aware = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone(timedelta(hours=3)))

    # The column type each backend is given.
    assert column.load_dialect_impl(pg).timezone is True
    assert getattr(column.load_dialect_impl(lite), "timezone", False) is False

    # Postgres keeps the zone -- and gets the INSTANT, not the wall clock.
    bound_pg = column.process_bind_param(aware, pg)
    assert bound_pg.tzinfo is not None
    assert bound_pg.utcoffset() == timedelta(0)
    assert bound_pg == aware

    # SQLite cannot hold a zone, so it gets naive UTC: today's storage format.
    # The two naive literals below carry DTZ001 waivers because being naive is
    # the property under test -- an aware version would assert the opposite.
    bound_lite = column.process_bind_param(aware, lite)
    assert bound_lite.tzinfo is None
    assert bound_lite == datetime(2026, 6, 15, 9, 0, 0)  # noqa: DTZ001

    # A naive input means UTC, and must be STAMPED before psycopg2 sees it --
    # an unstamped naive value reaching TIMESTAMPTZ is read as server-local.
    naive = datetime(2026, 6, 15, 9, 0, 0)  # noqa: DTZ001
    assert column.process_bind_param(naive, pg) == naive.replace(tzinfo=timezone.utc)

    # Reading back normalises to UTC on either dialect, including the aware
    # non-UTC value psycopg2 hands over when the session zone is not UTC.
    session_local = datetime(2026, 6, 15, 14, 30, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    for dialect in (pg, lite):
        assert column.process_result_value(session_local, dialect).utcoffset() == timedelta(0)
        assert column.process_result_value(session_local, dialect) == session_local
        assert column.process_result_value(naive, dialect) == naive.replace(tzinfo=timezone.utc)
        assert column.process_result_value(None, dialect) is None


def test_bind_param_converts_a_non_utc_offset_rather_than_stripping_it(db_session):
    """The exact bug class this decorator exists to avoid.

    SQLite's OWN `DateTime(timezone=True)` is broken in precisely this way --
    it strips tzinfo on write WITHOUT converting, so '12:00+03:00' is stored
    as '12:00' instead of the correct '09:00' UTC (see clock.py's module
    docstring and the plan). process_bind_param must call astimezone(utc)
    before dropping tzinfo, not just .replace(tzinfo=None) -- this test fails
    if someone "simplifies" it to the naive version, because every other test
    here only ever binds values already in UTC and would not catch that.
    """
    plus_three = timezone(timedelta(hours=3))
    local_noon = datetime(2026, 6, 15, 12, 0, 0, tzinfo=plus_three)  # == 09:00 UTC

    log = models.TransactionLog(event_type="OFFSET_TEST", timestamp=local_noon)
    db_session.add(log)
    db_session.commit()
    log_id = log.id
    db_session.expunge_all()

    raw = db_session.execute(
        text("SELECT timestamp FROM transaction_logs WHERE id = :id"), {"id": log_id}
    ).scalar_one()
    assert raw.startswith("2026-06-15 09:00:00"), f"stripped instead of converted: {raw!r}"

    reloaded = db_session.query(models.TransactionLog).filter_by(id=log_id).one()
    assert reloaded.timestamp == local_noon  # same instant, aware UTC on read


def test_none_passes_through_both_directions(db_session):
    """closed_at (nullable, no default) is the column this matters for --
    the decorator must not turn a legitimate NULL into an error or a bogus
    epoch value on either the write or the read side.
    """
    log = models.MaintenanceLog(equipment_id=None, fault_type_id=None, description="x")
    db_session.add(log)
    db_session.commit()
    log_id = log.id
    db_session.expunge_all()

    reloaded = db_session.query(models.MaintenanceLog).filter_by(id=log_id).one()
    assert reloaded.closed_at is None


# --- 4. The three arithmetic sites still classify correctly ------------------


def _make_equipment(db_session, last_verified_at):
    """A minimal Equipment row for exercising the compliance/report-status
    properties in isolation, without going through the full mock_matrix_db
    fixture and its eleven accounts.
    """
    import uuid

    from backend import authz

    unique = uuid.uuid4().hex
    cat = models.CatalogItem(name=f"Arithmetic Test Item {unique}")
    group = authz.Unit(name=f"arithmetic-test-group-{unique}")
    db_session.add_all([cat, group])
    db_session.commit()

    item = models.Equipment(
        catalog_item_id=cat.id,
        status="Functional",
        group_id=group.id,
        last_verified_at=last_verified_at,
    )
    db_session.add(item)
    db_session.commit()
    item_id = item.id
    db_session.expunge_all()
    return db_session.query(models.Equipment).filter_by(id=item_id).one()


@pytest.mark.parametrize(
    "hours_ago,expected",
    [(1, "GOOD"), (30, "WARNING"), (72, "SEVERE")],
)
def test_compliance_level_classifies_across_the_boundaries(db_session, hours_ago, expected):
    reloaded = _make_equipment(db_session, clock.utcnow() - timedelta(hours=hours_ago))
    assert reloaded.compliance_level == expected


@pytest.mark.parametrize(
    "hours_ago,expected",
    [(1, "דיווח תקין"), (30, "חריגת דיווח")],
)
def test_report_status_classifies_across_the_boundary(db_session, hours_ago, expected):
    """models.py:194 (report_status) is the SECOND arithmetic property named
    in the plan, alongside compliance_level -- distinct code, same
    clock.utcnow() subtraction, and previously untested here on its own.
    Checked by prefix rather than exact match: the overdue branch appends a
    computed day/hour count after this literal.
    """
    reloaded = _make_equipment(db_session, clock.utcnow() - timedelta(hours=hours_ago))
    assert reloaded.report_status.startswith(expected)


def test_compliance_level_at_the_exact_24_and_48_hour_boundaries(db_session):
    """`diff < timedelta(hours=24)` is a strict inequality -- exactly 24h00m00s
    since verification must already read as WARNING, not GOOD, and exactly
    48h00m00s must already read as SEVERE. A boundary this exact is worth
    pinning on its own rather than trusting the 1h/30h/72h samples above to
    imply it.
    """
    at_24h = _make_equipment(db_session, clock.utcnow() - timedelta(hours=24))
    assert at_24h.compliance_level == "WARNING"

    at_48h = _make_equipment(db_session, clock.utcnow() - timedelta(hours=48))
    assert at_48h.compliance_level == "SEVERE"


def test_get_daily_status_agrees_with_compliance_level(db_session):
    """dependencies.get_daily_status is a second implementation of the same
    boundaries (models.Equipment.compliance_level); both must still agree
    now that both go through clock.utcnow() instead of datetime.utcnow().
    """
    from backend.dependencies import get_daily_status

    now = clock.utcnow()
    assert get_daily_status(now - timedelta(hours=1)) == "GOOD"
    assert get_daily_status(now - timedelta(hours=30)) == "WARNING"
    assert get_daily_status(now - timedelta(hours=72)) == "SEVERE"
    assert get_daily_status(now - timedelta(hours=24)) == "WARNING"
    assert get_daily_status(now - timedelta(hours=48)) == "SEVERE"
    assert get_daily_status(None) == "SEVERE"


def test_iso_z_handles_none_naive_and_non_utc_input():
    """clock.iso_z() directly, at its three input shapes.

    The endpoint-level contract test below only ever feeds it values that
    came back through UtcDateTime (already aware UTC), so it cannot catch a
    regression in iso_z()'s own None/naive/offset handling -- this does.
    """
    assert clock.iso_z(None) is None

    naive = datetime(2026, 3, 1, 8, 0, 0)
    assert clock.iso_z(naive) == "2026-03-01T08:00:00Z"

    plus_five = timezone(timedelta(hours=5))
    offset = datetime(2026, 3, 1, 13, 0, 0, tzinfo=plus_five)  # == 08:00 UTC
    assert clock.iso_z(offset) == "2026-03-01T08:00:00Z"


# --- 5. The three non-plain-substitution sites -------------------------------


def test_bulk_update_writes_an_aware_correct_closed_at(client, mock_matrix_db, token_master, db_session):
    """maintenance.py:152 writes closed_at through Query.update(), not an ORM
    attribute assignment -- a different path through the bind processor than
    every other site here, and worth its own pin.
    """
    item = db_session.query(models.Equipment).filter_by(serial_number="SA100").one()
    item_id = item.id

    report = client.post(
        "/maintenance/report",
        json={"equipment_id": item_id, "fault_name": "Contract Test Fault", "description": "x"},
        headers=token_master,
    )
    assert report.status_code == 200, report.text

    before = clock.utcnow()
    fix = client.post(f"/maintenance/fix/{item_id}", headers=token_master)
    assert fix.status_code == 200, fix.text
    after = clock.utcnow()

    db_session.expunge_all()
    log = (
        db_session.query(models.MaintenanceLog)
        .filter_by(equipment_id=item_id, status="Closed")
        .order_by(models.MaintenanceLog.id.desc())
        .first()
    )
    assert log.closed_at.tzinfo is not None
    assert before <= log.closed_at <= after


def test_cutoff_comparison_selects_the_right_side_of_the_boundary(
    client, db_session, mock_matrix_db, token_master
):
    """reports.py:91's `cutoff` is a comparison bind against TransactionLog.timestamp,
    not a write -- the aware cutoff must convert correctly or the 24h filter on
    /reports/daily_movement compares an aware value against naive storage.
    """
    item = db_session.query(models.Equipment).filter_by(serial_number="SA100").one()

    inside = models.TransactionLog(
        equipment_id=item.id, event_type="INSIDE_WINDOW",
        timestamp=clock.utcnow() - timedelta(hours=1),
    )
    outside = models.TransactionLog(
        equipment_id=item.id, event_type="OUTSIDE_WINDOW",
        timestamp=clock.utcnow() - timedelta(hours=48),
    )
    db_session.add_all([inside, outside])
    db_session.commit()

    res = client.get("/reports/daily_movement", headers=token_master)
    assert res.status_code == 200, res.text
    event_types = {row["event_type"] for row in res.json()}
    assert "INSIDE_WINDOW" in event_types
    assert "OUTSIDE_WINDOW" not in event_types


def test_jwt_exp_claim_is_byte_identical_before_and_after(monkeypatch):
    """security.py's two `exp` lines moved to clock.utcnow() specifically so
    the grep guard above has no exceptions to carve out (see clock.py and the
    plan for why). This proves that move is a no-op: python-jose reduces `exp`
    via `timegm(value.utctimetuple())`, which normalises an aware value by its
    offset, so an aware UTC datetime and the naive value it replaced produce
    the identical encoded token.
    """
    from backend import security

    fixed_naive = datetime(2026, 1, 1, 12, 0, 0)
    fixed_aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(security, "clock", type("C", (), {"utcnow": staticmethod(lambda: fixed_naive)}))
    token_naive = security.create_access_token({"sub": "x"}, expires_delta=timedelta(minutes=15))

    monkeypatch.setattr(security, "clock", type("C", (), {"utcnow": staticmethod(lambda: fixed_aware)}))
    token_aware = security.create_access_token({"sub": "x"}, expires_delta=timedelta(minutes=15))

    assert token_naive == token_aware


# --- 6. The contract test: every timestamp-bearing endpoint, one format -----


def _parse_and_check_aware_utc(value: str):
    assert value is not None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)
    return value


def test_every_timestamp_bearing_endpoint_emits_one_aware_utc_format(
    client, db_session, mock_matrix_db, token_master
):
    """The real regression pin: build data through the API, collect every
    timestamp string the API emits, and assert (a) each parses as aware UTC
    and (b) all endpoints agree on the same designator.

    Deliberately driven through the API rather than asserted against ORM
    objects -- an ORM-level pass here would say nothing about what actually
    reaches the browser, which is the entire defect this ticket is about.
    """
    item = db_session.query(models.Equipment).filter_by(serial_number="SA100").one()
    soldier_header = create_auth_header("u_soldier_a")

    verify = client.post(f"/equipment/{item.id}/verify", headers=soldier_header)
    assert verify.status_code == 200, verify.text

    verification = client.post(
        "/verifications/",
        json={
            "equipment_id": item.id,
            "verification_type": "Routine",
            "reported_status": "Malfunctioning",
        },
        headers=token_master,
    )
    assert verification.status_code == 200, verification.text

    report = client.post(
        "/maintenance/report",
        json={"equipment_id": item.id, "fault_name": "Contract Sweep Fault", "description": "x"},
        headers=token_master,
    )
    assert report.status_code == 200, report.text
    fix = client.post(f"/maintenance/fix/{item.id}", headers=token_master)
    assert fix.status_code == 200, fix.text

    collected = []

    users_me = client.get("/users/me", headers=token_master)
    assert users_me.status_code == 200
    collected.append(users_me.json()["last_seen"])

    users_list = client.get("/users", headers=token_master)
    assert users_list.status_code == 200
    collected += [u["last_seen"] for u in users_list.json() if u["last_seen"]]

    reports_query = client.get("/reports/query", headers=token_master)
    assert reports_query.status_code == 200
    collected += [
        row["last_verified_at"] for row in reports_query.json() if row["last_verified_at"]
    ]

    daily_movement = client.get("/reports/daily_movement", headers=token_master)
    assert daily_movement.status_code == 200
    collected += [row["timestamp"] for row in daily_movement.json() if row["timestamp"]]

    verifications = client.get(f"/verifications/equipment/{item.id}", headers=token_master)
    assert verifications.status_code == 200
    collected += [v["created_date"] for v in verifications.json()]

    history = client.get(f"/equipment/{item.id}/history", headers=token_master)
    assert history.status_code == 200
    collected += [h["created_date"] for h in history.json()]

    tickets = client.get("/tickets/", headers=token_master)
    assert tickets.status_code == 200
    # opened_at joined this sweep with DATA-H2. It was not omitted by choice
    # before then -- the field was not on the wire at all, because
    # TicketResponse declared two aliases for the column and not the column.
    collected += [t["closed_at"] for t in tickets.json() if t["closed_at"]]
    collected += [t["opened_at"] for t in tickets.json() if t["opened_at"]]

    assert len(collected) >= 8, f"expected timestamps from every endpoint, got {collected}"

    for value in collected:
        _parse_and_check_aware_utc(value)

    # Every collected string must use the same ZONE DESIGNATOR ('Z' vs
    # '+00:00'), proving the un-modelled reports.py dicts (clock.iso_z) and
    # the Pydantic response_model routes agree on one wire format rather than
    # shipping two. Extracted with a regex rather than a fixed slice, since
    # fractional-second digits legitimately differ per timestamp.
    designators = {
        re.search(r"(Z|[+-]\d{2}:\d{2})$", value).group(1) for value in collected
    }
    assert len(designators) == 1, f"endpoints disagree on timestamp format: {designators}"
