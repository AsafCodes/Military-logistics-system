"""DATA-M9-2. The daily movement report is one group's calendar day, in that group's zone.

It was every movement visible to the caller in the 24 hours before the
request: a window that moved with the clock, straddled midnight, and could not
show a past day. Now `GET /reports/daily_movement?group_id=&date=` returns:

- the day: [start, end) between two local midnights of the group's zone
  (clock.day_bounds), so a day the clock changes on is 23 or 25 hours, and a
  stored zone tzdata no longer lists falls back to clock.DEFAULT_TIMEZONE;
- the rows filed under the group or anything it contains, through the
  group_id each row was written with (where the event left the item) or its
  from_group_id (where a move took it from). Both are fixed when the row is
  written, so a day that has passed does not change when the item moves on;
- the group: the one named, within the caller's VIEW (404 otherwise, one
  detail for every reason), or with none named the caller's one top VIEW
  group, 400 listing them when there are several.

The revision that files old rows (3c6a0f8e2d71) is at the end.
tests/test_movement_report.py holds the rows' contents and the contract;
tests/test_foreign_key_indexes.py the two indexes the query ranges over.
"""
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from backend import audit_trail, authz, clock, models
from backend.enums import Capability, EventType, Sensitivity
from backend.routers.reports import NO_GROUP
from backend.schemas import DB_INT_MAX
from tests.conftest import create_auth_header
from tests.test_group_schema import _downgrade, _schema_snapshot, _upgrade

URL = "/reports/daily_movement"
JERUSALEM, VIENNA = "Asia/Jerusalem", "Europe/Vienna"

# A past summer day, so every edge of it is behind the clock.
DAY = date(2026, 6, 10)


def _get(client, who, **params):
    return client.get(URL, params=params, headers=create_auth_header(who))


def _ok(client, who, **params):
    res = _get(client, who, **params)
    assert res.status_code == 200, res.text
    return res.json()


def _ids(client, who, **params):
    return [row["id"] for row in _ok(client, who, **params)["items"]]


def _item(db_session, serial="SA100"):
    return db_session.query(models.Equipment).filter_by(serial_number=serial).one()


def _local(zone_name, d, hour, minute=0):
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=ZoneInfo(zone_name))


def _log(db_session, at, group, *, from_group=None, event=EventType.HANDOVER, serial="SA100"):
    """A row as record_event writes one, at a chosen instant.

    `group` is group_id; a custody row also gets it as to_group_id.
    """
    custody = event in audit_trail.CUSTODY_EVENTS
    row = models.TransactionLog(
        equipment_id=_item(db_session, serial).id,
        event_type=event.value,
        timestamp=at,
        group_id=group.id,
        from_group_id=from_group.id if from_group else None,
        to_group_id=group.id if custody else None,
    )
    db_session.add(row)
    db_session.commit()
    return row.id


@pytest.fixture
def units(group_graph):
    return {
        "battalion": group_graph["188/53"],
        "a": group_graph["188/53/A"],
        "b": group_graph["188/53/B"],
    }


@pytest.fixture
def b_in_vienna(db_session, units):
    units["b"].timezone = VIENNA
    db_session.commit()
    return units


def _grant(db_session, users, who, capability, group):
    db_session.add(authz.Grant(user_id=users[who].id, group_id=group.id, capability=capability.value))
    db_session.commit()


# --- the day -------------------------------------------------------------------

@pytest.mark.parametrize("zone_name, unit, who", [(JERUSALEM, "a", "u_cmdr_a"), (VIENNA, "b", "u_cmdr_b")])
def test_a_minute_before_local_midnight_is_in_the_day_and_midnight_is_not(
    client, db_session, mock_matrix_db, b_in_vienna, zone_name, unit, who
):
    group = b_in_vienna[unit]
    late = _log(db_session, _local(zone_name, DAY, 23, 59), group, serial="SA100" if unit == "a" else "SB200")
    next_day = _log(
        db_session, _local(zone_name, DAY + timedelta(days=1), 0, 0), group,
        serial="SA100" if unit == "a" else "SB200",
    )

    assert _ids(client, who, date=DAY.isoformat()) == [late]
    assert _ids(client, who, date=(DAY + timedelta(days=1)).isoformat()) == [next_day]


@pytest.mark.parametrize("day, hours", [(date(2025, 3, 28), 23), (date(2025, 10, 26), 25)],
                         ids=["spring_forward", "fall_back"])
def test_a_day_the_clock_changes_on_holds_its_23_or_25_hours_of_rows(
    client, db_session, mock_matrix_db, units, day, hours
):
    """2025's two Jerusalem changes: a +24h bound would take an hour from the
    next day on the short day, and lose the last hour on the long one."""
    tz = ZoneInfo(JERUSALEM)
    start, end = clock.day_bounds(tz, day)
    assert end - start == timedelta(hours=hours), "the date is not a clock change; pick another"

    last_hour = _log(db_session, end - timedelta(minutes=30), units["a"])
    _log(db_session, end, units["a"])
    # The instant a +24h day would end: inside the long day, after the short one.
    plus_24 = _log(db_session, start + timedelta(hours=24) - timedelta(minutes=1), units["a"])

    got = set(_ids(client, "u_cmdr_a", date=day.isoformat()))
    if hours == 25:
        assert got == {last_hour, plus_24}
    else:
        assert got == {last_hour}


def test_the_envelope_names_the_day_s_bounds_in_utc(client, mock_matrix_db, units):
    report = _ok(client, "u_cmdr_a", date=DAY.isoformat())
    start, end = clock.day_bounds(ZoneInfo(JERUSALEM), DAY)

    assert (report["date"], report["timezone"]) == (DAY.isoformat(), JERUSALEM)
    assert (report["start"], report["end"]) == (
        start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def test_the_date_defaults_to_today_in_the_group_s_zone(client, mock_matrix_db, b_in_vienna):
    report = _ok(client, "u_cmdr_b")
    assert report["date"] == clock.today_in(ZoneInfo(VIENNA)).isoformat()
    assert report["timezone"] == VIENNA


def test_a_date_after_today_in_the_group_s_zone_is_refused(client, mock_matrix_db, units):
    tomorrow = clock.today_in(ZoneInfo(JERUSALEM)) + timedelta(days=1)
    for value in (tomorrow.isoformat(), "9999-12-31"):
        res = _get(client, "u_cmdr_a", date=value)
        assert res.status_code == 422, (value, res.text)


def test_a_date_whose_midnight_python_cannot_hold_is_refused_not_a_500(client, mock_matrix_db, units):
    """Year 1's first midnight in Jerusalem (LMT, east of UTC) is before
    datetime.min in UTC. There is no lower limit otherwise."""
    assert _get(client, "u_cmdr_a", date="0001-01-01").status_code == 422
    assert _get(client, "u_cmdr_a", date="0001-01-02").status_code == 200


def test_a_date_that_is_not_a_date_is_refused(client, mock_matrix_db):
    res = _get(client, "u_cmdr_a", date="2026-02-30")
    assert res.status_code == 422
    assert ["query", "date"] in [error["loc"] for error in res.json()["detail"]]


def test_a_stored_zone_that_tzdata_does_not_list_bounds_the_day_by_the_default(
    client, db_session, mock_matrix_db, units, caplog
):
    db_session.execute(text("UPDATE groups SET timezone = 'Mars/Olympus' WHERE id = :id"), {"id": units["a"].id})
    db_session.commit()

    with caplog.at_level(logging.WARNING, logger="backend.clock"):
        report = _ok(client, "u_cmdr_a", date=DAY.isoformat())

    start, _ = clock.day_bounds(ZoneInfo(clock.DEFAULT_TIMEZONE), DAY)
    assert report["timezone"] == clock.DEFAULT_TIMEZONE
    assert report["start"] == start.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert any("Mars/Olympus" in r.getMessage() for r in caplog.records), caplog.text


# --- the rows ------------------------------------------------------------------

def test_one_group_s_day_is_the_same_rows_for_every_viewer_of_it(
    client, db_session, mock_matrix_db, units
):
    """Root VIEW naming the company, and VIEW on the company alone."""
    rows = {_log(db_session, _local(JERUSALEM, DAY, h), units["a"]) for h in (8, 12, 20)}
    _log(db_session, _local(JERUSALEM, DAY, 9), units["b"], serial="SB200")

    by_root = _ids(client, "u_master", group_id=units["a"].id, date=DAY.isoformat())
    by_company = _ids(client, "u_cmdr_a", date=DAY.isoformat())
    assert set(by_root) == set(by_company) == rows
    assert by_root == by_company, "the order differs between viewers"


def test_rows_come_newest_first(client, db_session, mock_matrix_db, units):
    early = _log(db_session, _local(JERUSALEM, DAY, 8), units["a"])
    late = _log(db_session, _local(JERUSALEM, DAY, 20), units["a"])
    assert _ids(client, "u_cmdr_a", date=DAY.isoformat()) == [late, early]


def test_a_past_day_keeps_its_rows_when_the_item_moves_on(
    client, db_session, mock_matrix_db, units
):
    """Filed by the row, not by where the item is now (Asaf's answer 2)."""
    row = _log(db_session, _local(JERUSALEM, DAY, 12), units["a"])
    before = _ids(client, "u_cmdr_a", date=DAY.isoformat())

    item = _item(db_session)
    item.group_id = units["b"].id
    db_session.commit()

    assert before == [row]
    assert _ids(client, "u_cmdr_a", date=DAY.isoformat()) == [row]
    assert row not in _ids(client, "u_cmdr_b", date=DAY.isoformat())


def test_a_transfer_between_zones_is_in_both_groups_days_each_by_its_own_zone(
    client, db_session, mock_matrix_db, b_in_vienna
):
    """21:30Z on 10 June is 00:30 on the 11th in Jerusalem and 23:30 on the
    10th in Vienna, so the one row is on a different date in each."""
    units = b_in_vienna
    at = datetime(2026, 6, 10, 21, 30, tzinfo=ZoneInfo("UTC"))
    row = _log(db_session, at, units["b"], from_group=units["a"])
    june_10, june_11 = DAY.isoformat(), (DAY + timedelta(days=1)).isoformat()

    assert _ids(client, "u_master", group_id=units["a"].id, date=june_11) == [row]
    assert _ids(client, "u_master", group_id=units["a"].id, date=june_10) == []
    assert _ids(client, "u_master", group_id=units["b"].id, date=june_10) == [row]
    assert _ids(client, "u_master", group_id=units["b"].id, date=june_11) == []


def test_a_move_between_two_groups_inside_the_one_viewed_is_one_row(
    client, db_session, mock_matrix_db, units
):
    """It matches both arms. UNION, not UNION ALL."""
    row = _log(db_session, _local(JERUSALEM, DAY, 12), units["b"], from_group=units["a"])
    assert _ids(client, "u_bat_cmdr", date=DAY.isoformat()) == [row]


def test_a_fault_is_filed_under_the_item_s_group(client, db_session, mock_matrix_db, units):
    """A non-custody event has no from_ or to_ group; group_id alone files it."""
    res = client.post(
        "/maintenance/report",
        json={"equipment_id": _item(db_session).id, "fault_name": "Cracked", "description": "x"},
        headers=create_auth_header("u_soldier_a"),
    )
    assert res.status_code == 200, res.text

    rows = [r for r in _ok(client, "u_cmdr_a")["items"] if r["event_type"] == "FAULT"]
    assert [r["serial_number"] for r in rows] == ["SA100"]
    assert [r for r in _ok(client, "u_cmdr_b")["items"] if r["event_type"] == "FAULT"] == []


def test_record_event_files_every_row_under_the_group_the_event_left_the_item_in(
    client, db_session, mock_matrix_db, units
):
    """A custody event's group_id is its to_group_id; any other event's is the
    item's group. An assignment into company B moves the item there."""
    item = _item(db_session, "TA300")
    res = client.post(
        "/equipment/assign_owner/",
        json={"equipment_id": item.id, "owner_id": mock_matrix_db["soldier_b"].id},
        headers=create_auth_header("u_bat_cmdr"),
    )
    assert res.status_code == 200, res.text
    audit_trail.record_event(
        db_session, equipment=item, actor=mock_matrix_db["master"], event_type=EventType.FIX,
    )
    db_session.commit()

    rows = {
        log.event_type: log
        for log in db_session.query(models.TransactionLog).filter_by(equipment_id=item.id)
    }
    assign, fix = rows["ASSIGN"], rows["FIX"]
    assert (assign.from_group_id, assign.to_group_id, assign.group_id) == (
        units["a"].id, units["b"].id, units["b"].id
    )
    assert (fix.to_group_id, fix.group_id) == (None, units["b"].id)


def test_a_row_with_no_group_is_in_no_day(client, db_session, mock_matrix_db, units):
    """What 3c6a0f8e2d71 leaves on a row with no item, and what a hand-built
    row without one gets."""
    db_session.add(models.TransactionLog(
        equipment_id=_item(db_session).id, event_type="HANDOVER", timestamp=_local(JERUSALEM, DAY, 12),
    ))
    db_session.commit()
    assert _ids(client, "u_master", group_id=units["a"].id, date=DAY.isoformat()) == []


# --- classification, per row -------------------------------------------------

def test_a_classified_item_s_row_needs_clearance_over_the_group_it_is_filed_under(
    client, db_session, mock_matrix_db, units
):
    """DATA-H3-3's rule, asked of the row's group. The item is in company B
    now; the row is A's. Clearance over A shows it, and clearance over B,
    where the item is, does not."""
    row = _log(db_session, _local(JERUSALEM, DAY, 12), units["a"])
    item = _item(db_session)
    item.sensitivity = Sensitivity.CLASSIFIED.value
    item.group_id = units["b"].id
    db_session.commit()
    users = mock_matrix_db

    def visible(who):
        return row in _ids(client, who, group_id=units["a"].id, date=DAY.isoformat())

    assert not visible("u_cmdr_a")
    _grant(db_session, users, "company_cmdr_a", Capability.VIEW_CLASSIFIED, units["b"])
    assert not visible("u_cmdr_a")
    _grant(db_session, users, "company_cmdr_a", Capability.VIEW_CLASSIFIED, units["a"])
    assert visible("u_cmdr_a")
    # VIEW_CLASSIFIED over the battalion, which contains A.
    assert visible("u_bat_cmdr")


def test_clearance_is_asked_of_the_arm_that_found_the_row(
    client, db_session, mock_matrix_db, units
):
    """A classified item moved from A to B is in A's day through from_group_id
    and in B's through group_id. Clearance over A alone shows it in A's day,
    not in B's."""
    row = _log(db_session, _local(JERUSALEM, DAY, 12), units["b"], from_group=units["a"])
    item = _item(db_session)
    item.sensitivity = Sensitivity.CLASSIFIED.value
    db_session.commit()
    users = mock_matrix_db
    _grant(db_session, users, "company_cmdr_a", Capability.VIEW, units["b"])
    _grant(db_session, users, "company_cmdr_a", Capability.VIEW_CLASSIFIED, units["a"])

    assert row in _ids(client, "u_cmdr_a", group_id=units["a"].id, date=DAY.isoformat())
    assert row not in _ids(client, "u_cmdr_a", group_id=units["b"].id, date=DAY.isoformat())


# --- the group -----------------------------------------------------------------

@pytest.mark.parametrize(
    "who, target",
    [("u_cmdr_b", "a"), ("u_cmdr_a", "unknown"), ("u_soldier_a", None), ("u_soldier_a", "a")],
    ids=["outside_the_extent", "unknown", "no_view_at_all", "no_view_named"],
)
def test_a_group_the_caller_cannot_view_is_one_404(client, mock_matrix_db, units, who, target):
    params = {} if target is None else {"group_id": DB_INT_MAX if target == "unknown" else units[target].id}
    res = _get(client, who, **params)
    assert res.status_code == 404, res.text
    assert res.json() == {"detail": NO_GROUP}


def test_with_no_group_named_the_caller_s_one_top_view_group_is_used(
    client, mock_matrix_db, group_graph
):
    assert _ok(client, "u_master")["group_name"] == "188"
    assert _ok(client, "u_bat_cmdr")["group_name"] == "188/53"
    assert _ok(client, "u_cmdr_b")["group_name"] == "188/53/B"


def test_the_default_follows_view_not_membership(client, db_session, mock_matrix_db, units):
    """A battalion commander who sits in a company views the battalion."""
    user = mock_matrix_db["bat_cmdr"]
    db_session.query(authz.GroupMembership).filter_by(user_id=user.id).update({"group_id": units["a"].id})
    db_session.commit()

    assert _ok(client, "u_bat_cmdr")["group_id"] == units["battalion"].id


def test_a_view_grant_inside_another_adds_no_second_top(client, db_session, mock_matrix_db, units):
    _grant(db_session, mock_matrix_db, "company_cmdr_a", Capability.VIEW, units["battalion"])
    assert _ok(client, "u_cmdr_a")["group_id"] == units["battalion"].id


def test_several_top_view_groups_and_none_named_is_a_400_that_lists_them(
    client, db_session, mock_matrix_db, units
):
    _grant(db_session, mock_matrix_db, "company_cmdr_a", Capability.VIEW, units["b"])

    res = _get(client, "u_cmdr_a")
    assert res.status_code == 400, res.text
    body = res.json()
    assert set(body) == {"detail", "groups"}
    assert body["groups"] == [
        {"id": units["a"].id, "name": "188/53/A"},
        {"id": units["b"].id, "name": "188/53/B"},
    ]
    for unit in ("a", "b"):
        assert _ok(client, "u_cmdr_a", group_id=units[unit].id)["group_id"] == units[unit].id


def test_an_anonymous_caller_is_refused(client, mock_matrix_db):
    assert client.get(URL).status_code == 401


# --- deleting an item with history ---------------------------------------------

def test_an_item_with_a_logged_event_cannot_be_deleted(db_session, mock_matrix_db, units):
    """No history is lost to a deletion: equipment_id is RESTRICT (DATA-H13-2),
    so the rows that file a day stay. Postgres:
    tests/test_utc_migration_postgres.py."""
    template = _item(db_session)

    def fresh(serial):
        item = models.Equipment(
            catalog_item_id=template.catalog_item_id, group_id=units["a"].id,
            status="Functional", serial_number=serial,
        )
        db_session.add(item)
        db_session.commit()
        return item.id

    def delete(item_id):
        db_session.execute(text("DELETE FROM equipment WHERE id = :id"), {"id": item_id})
        db_session.commit()

    # The control: nothing else refers to a fresh item, so only the log row
    # can be what refuses the delete below.
    delete(fresh("DEL-0"))

    logged = fresh("DEL-1")
    _log(db_session, _local(JERUSALEM, DAY, 12), units["a"], event=EventType.FAULT, serial="DEL-1")
    with pytest.raises(IntegrityError):
        delete(logged)
    db_session.rollback()


# --- the revision ----------------------------------------------------------------

REVISION, PARENT = "3c6a0f8e2d71", "9d4e2b7c1a60"
NEW_INDEXES = {"ix_transaction_logs_group_id_timestamp", "ix_transaction_logs_from_group_id_timestamp"}
OLD_INDEXES = {"ix_transaction_logs_from_group_id", "ix_transaction_logs_timestamp"}

LEGACY_ROWS = [
    (
        "INSERT INTO groups (id, name, kind, timezone) VALUES "
        "(1, '188', 'UNIT', 'Asia/Jerusalem'), (2, '188/53', 'UNIT', 'Asia/Jerusalem'), "
        "(3, '188/54', 'UNIT', 'Asia/Jerusalem')"
    ),
    "INSERT INTO catalog_items (id, name) VALUES (1, 'M4')",
    "INSERT INTO equipment (id, catalog_item_id, group_id) VALUES (1, 1, 2)",
    # (log id, what it is)
    # 1: a custody row from DATA-M8-1 on, which left the item in group 3
    # 2: a fault
    # 3: a custody row from before DATA-M8-1, with no to_group_id
    # 4: a row with no item
    (
        "INSERT INTO transaction_logs (id, event_type, equipment_id, from_group_id, to_group_id) VALUES "
        "(1, 'ASSIGN', 1, 2, 3), (2, 'FAULT', 1, NULL, NULL), (3, 'HANDOVER', 1, NULL, NULL), "
        "(4, 'HANDOVER', NULL, NULL, NULL)"
    ),
]


def _indexes(engine):
    return {ix["name"] for ix in inspect(engine).get_indexes("transaction_logs")}


def _legacy_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm9_2.db'}")
    _upgrade(engine, PARENT)
    with engine.begin() as conn:
        for statement in LEGACY_ROWS:
            conn.exec_driver_sql(statement)
    return engine


def test_the_revision_files_each_old_row_under_a_group(tmp_path):
    """Exact for a custody row with a to_group_id; the item's group at
    migration time for the rest; NULL with no item."""
    engine = _legacy_db(tmp_path)
    _upgrade(engine, REVISION)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, group_id FROM transaction_logs ORDER BY id")).all()
    engine.dispose()

    assert rows == [(1, 3), (2, 2), (3, 2), (4, None)]


def test_the_revision_adds_the_key_and_trades_two_indexes_for_two(tmp_path):
    engine = _legacy_db(tmp_path)
    before = _indexes(engine)
    _upgrade(engine, REVISION)
    after = _indexes(engine)
    keys = {fk["name"]: fk for fk in inspect(engine).get_foreign_keys("transaction_logs")}
    with engine.connect() as conn:
        dangling = conn.exec_driver_sql("PRAGMA foreign_key_check").all()
    engine.dispose()

    assert OLD_INDEXES <= before and not NEW_INDEXES & before
    assert after == (before - OLD_INDEXES) | NEW_INDEXES
    key = keys["fk_transaction_logs_group_id"]
    assert (key["referred_table"], key["constrained_columns"], key["options"].get("ondelete")) == (
        "groups", ["group_id"], "RESTRICT"
    )
    assert dangling == []


def test_a_create_all_database_has_none_of_the_replaced_indexes_after_the_chain_re_runs(tmp_path):
    """f4d81b2c6a93 and b7e3d91c4f2a skip the index this revision replaced
    once its replacement exists, so re-running them does not bring it back.
    (test_group_schema re-runs every allowlisted revision; this names the two
    indexes.)"""
    from alembic.script import ScriptDirectory

    from backend import migrations
    from tests.test_group_schema import _rerun_upgrade

    engine = create_engine(f"sqlite:///{tmp_path / 'modern.db'}")
    models.Base.metadata.create_all(bind=engine)
    script = ScriptDirectory.from_config(migrations.alembic_config())
    for revision in ("f4d81b2c6a93", "b7e3d91c4f2a", REVISION):
        with engine.begin() as conn:
            _rerun_upgrade(conn, script, revision)
    present = _indexes(engine)
    engine.dispose()

    assert NEW_INDEXES <= present
    assert not OLD_INDEXES & present


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
