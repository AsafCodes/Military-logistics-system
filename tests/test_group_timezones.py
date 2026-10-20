"""DATA-M9-1: every group keeps its calendar day in a zone of its own.

What is pinned here:

- clock's zone helpers: the strict check write paths use, the fallback read
  paths use, and day_bounds, which must follow the clock across a DST change
  rather than add 24 hours;
- groups.timezone: NOT NULL, no default, and the revision that adds it fills
  the groups that already exist and then drops the default;
- the writers: bootstrap_admin's root takes BOOTSTRAP_ROOT_TIMEZONE, checked
  before anything is written (the seed's groups are checked in
  test_seed_graph.py, which runs the seed);
- PATCH /groups/{group_id}: gated on MANAGE_PERSONNEL before the lookup, a
  bad zone or an extra field refused with nothing changed, and GET /groups
  carrying the zone.

The report that bounds a day by these zones is DATA-M9-2's.
"""
import logging
from datetime import date, datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend import authz, bootstrap_admin, clock, migrations, models
from tests.conftest import create_auth_header
from tests.test_group_schema import _downgrade, _upgrade

REVISION = "9d4e2b7c1a60"
PARENT = "b7e3d91c4f2a"

HOURS = timedelta(hours=1)


# --- the zone helpers ---------------------------------------------------------

@pytest.mark.parametrize(
    "zone_name, short_day, long_day",
    [
        # Measured with zoneinfo (tzdata 2026e) when this was written: the
        # days of 2026 whose two midnights are not 24 hours apart.
        ("Asia/Jerusalem", date(2026, 3, 27), date(2026, 10, 25)),
        ("Europe/Vienna", date(2026, 3, 29), date(2026, 10, 25)),
    ],
)
def test_a_day_that_changes_the_clock_is_23_or_25_hours(zone_name, short_day, long_day):
    tz = clock.zone(zone_name)

    start, end = clock.day_bounds(tz, short_day)
    assert end - start == 23 * HOURS
    start, end = clock.day_bounds(tz, long_day)
    assert end - start == 25 * HOURS

    # And the days on either side are the ordinary 24, so the extra or the
    # missing hour lands on the day the clock changed and on no other.
    for d in (short_day, long_day):
        for neighbour in (d - timedelta(days=1), d + timedelta(days=1)):
            start, end = clock.day_bounds(tz, neighbour)
            assert end - start == 24 * HOURS, neighbour


@pytest.mark.parametrize("zone_name", ["Asia/Jerusalem", "Europe/Vienna", "America/Santiago"])
def test_each_day_begins_where_the_one_before_it_ends(zone_name):
    """Every instant belongs to exactly one day: no gap, no overlap.

    Santiago is here because it changes the clock AT midnight, so on
    2026-09-06 the midnight that starts the day never happens on the wall.
    """
    tz = clock.zone(zone_name)
    d = date(2026, 1, 1)
    while d.year == 2026:
        assert clock.day_bounds(tz, d)[1] == clock.day_bounds(tz, d + timedelta(days=1))[0], d
        d += timedelta(days=1)


def test_a_day_is_bounded_by_local_midnight_and_returned_in_utc():
    start, end = clock.day_bounds(clock.zone("Europe/Vienna"), date(2026, 7, 1))

    assert start == datetime(2026, 6, 30, 22, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 7, 1, 22, 0, tzinfo=timezone.utc)
    assert start.utcoffset() == end.utcoffset() == timedelta(0)


def test_a_skipped_midnight_starts_the_day_at_the_first_instant_on_it():
    """America/Santiago went from 23:59:59 on 2026-09-05 straight to 01:00."""
    tz = clock.zone("America/Santiago")
    start, _ = clock.day_bounds(tz, date(2026, 9, 6))

    assert start.astimezone(tz).replace(tzinfo=None) == datetime(2026, 9, 6, 1, 0)
    assert (start - timedelta(seconds=1)).astimezone(tz).date() == date(2026, 9, 5)


def test_today_is_the_date_on_the_zones_own_calendar(monkeypatch):
    """23:30 UTC on 30 June is already 1 July in Jerusalem, still 30 June in UTC."""
    instant = datetime(2026, 6, 30, 23, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(clock, "utcnow", lambda: instant)

    assert clock.today_in(clock.zone("Asia/Jerusalem")) == date(2026, 7, 1)
    assert clock.today_in(clock.zone("UTC")) == date(2026, 6, 30)


@pytest.mark.parametrize(
    "name",
    [
        "Mars/Olympus",
        "",
        # Both of these load through ZoneInfo on Windows, where names are
        # looked up as files of the tzdata package, and neither is in the
        # zone list. Accepting them would store a name another machine refuses.
        "utc",
        "Asia/Jerusalem ",
        "asia/jerusalem",
        "../etc/passwd",
        "localtime",
        None,
        5,
        # Unhashable: without the type check the set lookup raises TypeError.
        ["Europe/Vienna"],
    ],
)
def test_the_strict_check_refuses_a_name_outside_the_zone_list(name):
    with pytest.raises(ValueError):
        clock.zone(name)


def test_the_strict_list_is_the_locked_packages_not_the_machines(tmp_path):
    """zoneinfo.available_timezones() also lists every zone file under the
    system's zone directories -- on Linux that adds whatever else sits there,
    such as a `localtime` file. So the strict check reads the tzdata
    package's own list, which the hash lock holds to one version everywhere.

    Simulated here by pointing zoneinfo at a directory that holds a valid
    zone file named `localtime`, as a Linux system directory can.
    """
    import zoneinfo

    zone_file = files("tzdata.zoneinfo.Europe").joinpath("Vienna").read_bytes()
    (tmp_path / "localtime").write_bytes(zone_file)
    zoneinfo.reset_tzpath([str(tmp_path)])
    clock._known_zones.cache_clear()
    try:
        assert "localtime" in zoneinfo.available_timezones()
        with pytest.raises(ValueError):
            clock.zone("localtime")
        assert str(clock.zone("Europe/Vienna")) == "Europe/Vienna"
    finally:
        zoneinfo.reset_tzpath()
        clock._known_zones.cache_clear()


def test_the_default_is_a_zone_the_strict_check_accepts():
    assert str(clock.zone(clock.DEFAULT_TIMEZONE)) == clock.DEFAULT_TIMEZONE


def test_a_stored_name_the_zone_list_lacks_falls_back_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger=clock.logger.name):
        tz, effective = clock.zone_or_default("Mars/Olympus")

    assert effective == clock.DEFAULT_TIMEZONE
    assert str(tz) == clock.DEFAULT_TIMEZONE
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "Mars/Olympus" in warnings[0].getMessage()


def test_a_known_stored_name_is_used_and_logs_nothing(caplog):
    with caplog.at_level(logging.WARNING, logger=clock.logger.name):
        tz, effective = clock.zone_or_default("Europe/Vienna")

    assert (str(tz), effective) == ("Europe/Vienna", "Europe/Vienna")
    assert caplog.records == []


# --- the column ---------------------------------------------------------------

def test_the_column_is_not_null_and_has_no_default(db_session):
    column = {c["name"]: c for c in inspect(db_session.get_bind()).get_columns("groups")}["timezone"]
    assert column["nullable"] is False
    assert column["default"] is None


def test_a_group_written_without_a_zone_is_refused(db_session):
    with pytest.raises(IntegrityError):
        db_session.execute(text("INSERT INTO groups (name, kind) VALUES ('nowhere', 'UNIT')"))
    db_session.rollback()

    # And through the ORM: the model has no Python default either.
    db_session.add(authz.Unit(name="nowhere"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# --- the revision -------------------------------------------------------------

def _groups_columns(engine):
    return {c["name"]: c for c in inspect(engine).get_columns("groups")}


def test_the_revision_gives_existing_groups_the_default_zone(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm9.db'}")
    _upgrade(engine, PARENT)
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO groups (id, name, kind) VALUES (1, '188', 'UNIT')")
        conn.exec_driver_sql("INSERT INTO groups (id, name, kind) VALUES (2, '188/53', 'UNIT')")
        conn.exec_driver_sql("INSERT INTO group_edges (parent_id, child_id) VALUES (1, 2)")

    _upgrade(engine, REVISION)

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, timezone FROM groups ORDER BY id")).all()
        edges = conn.execute(text("SELECT parent_id, child_id FROM group_edges")).all()
    column = _groups_columns(engine)["timezone"]
    engine.dispose()

    assert rows == [(1, "Asia/Jerusalem"), (2, "Asia/Jerusalem")]
    # The SQLite half rebuilds the table to drop the default; what refers to
    # the groups must survive that.
    assert edges == [(1, 2)]
    assert column["nullable"] is False
    assert column["default"] is None


def test_the_rebuild_keeps_every_row_that_refers_to_a_group(tmp_path):
    """On SQLite the revision rebuilds `groups` to drop the default: a copy,
    a DROP of the original, a rename. Nine foreign keys point at it. With
    keys enforced during that DROP, the rebuild deletes the groups'
    memberships, closure and edges by their CASCADE rules, and refuses
    once an item points at a group (RESTRICT) -- both measured. The
    migration engine does not enforce them (backend.migrations), and this
    test migrates the same way, through a connection SQLite leaves
    unenforced by default. What it pins is the result: every referring row
    is still there, and still points at a group that exists.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'm9.db'}")
    _upgrade(engine, PARENT)
    statements = [
        "INSERT INTO groups (id, name, kind) VALUES (1, '188', 'UNIT'), (2, '188/53', 'UNIT')",
        "INSERT INTO group_edges (parent_id, child_id) VALUES (1, 2)",
        "INSERT INTO group_closure (ancestor_id, descendant_id, depth) VALUES (1, 1, 0), (2, 2, 0), (1, 2, 1)",
        "INSERT INTO users (id, personal_number) VALUES (1, 'u1')",
        "INSERT INTO group_memberships (user_id, group_id) VALUES (1, 2)",
        "INSERT INTO grants (id, user_id, group_id, capability) VALUES (1, 1, 1, 'VIEW')",
        "INSERT INTO catalog_items (id, name) VALUES (1, 'M4')",
        "INSERT INTO equipment (id, catalog_item_id, group_id) VALUES (1, 1, 2)",
        (
            "INSERT INTO transaction_logs (id, event_type, equipment_id, from_group_id, to_group_id) "
            "VALUES (1, 'HANDOVER', 1, 2, 1)"
        ),
    ]
    tables = ["group_edges", "group_closure", "group_memberships", "grants", "equipment", "transaction_logs"]
    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)

    def snapshot():
        with engine.connect() as conn:
            return {t: conn.exec_driver_sql(f"SELECT * FROM {t} ORDER BY 1").all() for t in tables}

    before = snapshot()
    _upgrade(engine, REVISION)
    after = snapshot()
    with engine.connect() as conn:
        dangling = conn.exec_driver_sql("PRAGMA foreign_key_check").all()
    engine.dispose()

    assert after == before
    assert all(before.values()), "a table was seeded empty, so the comparison proves nothing for it"
    assert dangling == []


def test_the_revision_on_an_empty_database_leaves_no_default_behind(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm9.db'}")
    _upgrade(engine, REVISION)

    with engine.begin() as conn, pytest.raises(IntegrityError):
        conn.exec_driver_sql("INSERT INTO groups (name, kind) VALUES ('nowhere', 'UNIT')")
    engine.dispose()


def test_the_backfill_is_a_frozen_copy_of_the_default():
    """The revision's literal and clock's constant agree today.

    If DEFAULT_TIMEZONE changes, this fails, and the right fix is to change
    this test, not the revision: what an applied revision did must not move.
    """
    source = (
        Path(migrations.PROJECT_ROOT) / "alembic" / "versions" / "9d4e2b7c1a60_group_time_zones.py"
    ).read_text(encoding="utf-8")
    assert f"BACKFILL = '{clock.DEFAULT_TIMEZONE}'" in source


def test_the_downgrade_drops_the_column(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm9.db'}")
    _upgrade(engine, REVISION)
    _downgrade(engine, PARENT)
    columns = _groups_columns(engine)
    engine.dispose()

    assert "timezone" not in columns


# --- bootstrap ----------------------------------------------------------------

@pytest.fixture
def bootstrap_db(tmp_path, monkeypatch):
    """bootstrap_admin() against a throwaway database, with its migrations
    replaced by create_all and recorded, so a test can tell whether the run
    got as far as writing anything."""
    engine = create_engine(f"sqlite:///{tmp_path / 'boot.db'}")
    Session = sessionmaker(bind=engine)
    migrated = []

    def fake_migrations():
        migrated.append(True)
        models.Base.metadata.create_all(bind=engine)

    monkeypatch.setenv("BOOTSTRAP_ADMIN_ENABLED", "1")
    monkeypatch.delenv(bootstrap_admin.ROOT_TIMEZONE_ENV, raising=False)
    monkeypatch.setattr(bootstrap_admin, "run_migrations", fake_migrations)
    monkeypatch.setattr(bootstrap_admin, "SessionLocal", Session)
    yield Session, migrated
    engine.dispose()


def _root_zone(Session):
    with Session() as db:
        return [(g.name, g.timezone) for g in db.query(authz.Group).all()]


@pytest.mark.parametrize("setting, expected", [(None, "Asia/Jerusalem"), ("", "Asia/Jerusalem"),
                                               ("Europe/Vienna", "Europe/Vienna")])
def test_the_bootstrapped_root_takes_the_zone_from_the_environment(
    bootstrap_db, monkeypatch, capsys, setting, expected
):
    Session, _ = bootstrap_db
    if setting is not None:
        monkeypatch.setenv(bootstrap_admin.ROOT_TIMEZONE_ENV, setting)

    bootstrap_admin.bootstrap_admin("9000001", "Root Admin")
    capsys.readouterr()  # the generated password, kept out of the report

    assert _root_zone(Session) == [(bootstrap_admin.ROOT_GROUP_NAME, expected)]


def test_an_unknown_root_zone_stops_the_run_before_anything_is_written(
    bootstrap_db, monkeypatch, capsys
):
    _, migrated = bootstrap_db
    monkeypatch.setenv(bootstrap_admin.ROOT_TIMEZONE_ENV, "Mars/Olympus")

    with pytest.raises(SystemExit) as refused:
        bootstrap_admin.bootstrap_admin("9000001", "Root Admin")
    capsys.readouterr()

    assert "Mars/Olympus" in str(refused.value)
    assert bootstrap_admin.ROOT_TIMEZONE_ENV in str(refused.value)
    assert migrated == []


@pytest.mark.parametrize("setting", ["Mars/Olympus", " Europe/Vienna", "europe/vienna"])
def test_an_unknown_root_zone_is_refused_even_where_no_root_will_be_made(
    bootstrap_db, monkeypatch, capsys, setting
):
    """A database that already has a root ignores the setting -- and a run
    given a bad one still stops, rather than succeeding with the mistake
    unreported. Nothing is written: no account, no grant."""
    Session, migrated = bootstrap_db
    bootstrap_admin.run_migrations()  # the schema, as an earlier run left it
    with Session() as db:
        db.add(authz.Unit(name="HQ", timezone="Europe/Vienna"))
        db.commit()
    migrated.clear()
    monkeypatch.setenv(bootstrap_admin.ROOT_TIMEZONE_ENV, setting)

    with pytest.raises(SystemExit):
        bootstrap_admin.bootstrap_admin("9000001", "Root Admin")
    capsys.readouterr()

    assert migrated == []
    with Session() as db:
        assert db.query(models.User).count() == 0
        assert db.query(authz.Grant).count() == 0
        assert [(g.name, g.timezone) for g in db.query(authz.Group)] == [("HQ", "Europe/Vienna")]


def test_an_existing_root_keeps_its_own_zone(db_session, group_graph):
    admin = models.User(personal_number="9000001", full_name="Root Admin",
                        password_hash="x", is_active_duty=True)
    db_session.add(admin)
    db_session.commit()

    bootstrap_admin.grant_root_authority(db_session, admin, "Europe/Vienna")

    assert {g.timezone for g in db_session.query(authz.Group)} == {clock.DEFAULT_TIMEZONE}


# --- the API ------------------------------------------------------------------

def _group(db_session, name):
    return db_session.query(authz.Group).filter_by(name=name).one()


def test_the_group_list_carries_each_groups_zone(client, mock_matrix_db):
    res = client.get("/groups", headers=create_auth_header("u_master"))

    assert res.status_code == 200
    assert {g["timezone"] for g in res.json()} == {clock.DEFAULT_TIMEZONE}
    assert all(set(g) == {"id", "name", "kind", "timezone"} for g in res.json())


def test_the_personnel_verb_sets_a_groups_zone(client, db_session, mock_matrix_db):
    company = _group(db_session, "188/53/A")

    res = client.patch(f"/groups/{company.id}", json={"timezone": "Europe/Vienna"},
                       headers=create_auth_header("u_master"))

    assert res.status_code == 200
    assert res.json() == {"id": company.id, "name": "188/53/A", "kind": "UNIT",
                          "timezone": "Europe/Vienna"}
    db_session.expire_all()
    zones = {g.name: g.timezone for g in db_session.query(authz.Group)}
    # Only that group: its parent and its sibling keep theirs.
    assert zones == {"188": clock.DEFAULT_TIMEZONE, "188/53": clock.DEFAULT_TIMEZONE,
                     "188/53/A": "Europe/Vienna", "188/53/B": clock.DEFAULT_TIMEZONE}
    listed = client.get("/groups", headers=create_auth_header("u_master")).json()
    assert {g["name"]: g["timezone"] for g in listed}["188/53/A"] == "Europe/Vienna"


@pytest.mark.parametrize(
    "body",
    [
        {"timezone": "Mars/Olympus"},
        {"timezone": "utc"},
        {"timezone": ""},
        {"timezone": None},
        {},
        # A field the body does not name: refused, not ignored.
        {"timezone": "Europe/Vienna", "name": "renamed"},
        {"timezone": "Europe/Vienna", "kind": "TASK_FORCE"},
        {"timezone": "Europe/Vienna", "id": 1},
        {"timezone": 5},
        {"timezone": ["Europe/Vienna"]},
        {"timezone": " Europe/Vienna"},
        {"timezone": "Europe/Vienna" * 1000},
        ["Europe/Vienna"],
        "Europe/Vienna",
    ],
)
def test_a_bad_body_is_refused_and_changes_nothing(client, db_session, mock_matrix_db, body):
    company = _group(db_session, "188/53/A")

    res = client.patch(f"/groups/{company.id}", json=body, headers=create_auth_header("u_master"))

    assert res.status_code == 422
    db_session.expire_all()
    assert (company.name, company.kind, company.timezone) == ("188/53/A", "UNIT", clock.DEFAULT_TIMEZONE)


@pytest.mark.parametrize("who", ["u_brig_cmdr", "u_cmdr_a", "u_soldier_a"])
def test_without_the_personnel_verb_the_zone_cannot_be_set(client, db_session, mock_matrix_db, who):
    """u_brig_cmdr holds MANAGE_CATALOG over the root, the other global verb,
    and not MANAGE_PERSONNEL: a gate on the wrong verb lets them through."""
    company = _group(db_session, "188/53/A")

    res = client.patch(f"/groups/{company.id}", json={"timezone": "Europe/Vienna"},
                       headers=create_auth_header(who))

    assert res.status_code == 403
    db_session.expire_all()
    assert company.timezone == clock.DEFAULT_TIMEZONE


def test_the_gate_runs_before_the_lookup(client, mock_matrix_db):
    """A caller without the verb gets 403 for an id that does not exist, as
    for one that does, so the answer tells them nothing about which ids exist."""
    body = {"timezone": "Europe/Vienna"}

    assert client.patch("/groups/999999", json=body,
                        headers=create_auth_header("u_cmdr_a")).status_code == 403
    res = client.patch("/groups/999999", json=body, headers=create_auth_header("u_master"))
    assert res.status_code == 404
    assert res.json() == {"detail": "Group not found"}


def test_an_anonymous_caller_is_refused(client, db_session, mock_matrix_db):
    company = _group(db_session, "188/53/A")

    res = client.patch(f"/groups/{company.id}", json={"timezone": "Europe/Vienna"})

    assert res.status_code == 401


def test_a_path_that_is_not_a_group_id_is_refused(client, mock_matrix_db):
    res = client.patch("/groups/abc", json={"timezone": "Europe/Vienna"},
                       headers=create_auth_header("u_master"))
    assert res.status_code == 422


def test_a_stored_zone_that_left_the_zone_database_still_lists_and_can_be_replaced(
    client, db_session, mock_matrix_db
):
    """The read path does not re-check: a name stored before IANA retired it
    is sent as stored, rather than failing GET /groups for every group. And
    PATCH accepts the group, so the bad name can be set right."""
    company = _group(db_session, "188/53/A")
    db_session.execute(text("UPDATE groups SET timezone = 'Mars/Olympus' WHERE id = :id"), {"id": company.id})
    db_session.commit()

    listed = client.get("/groups", headers=create_auth_header("u_master"))
    assert listed.status_code == 200
    assert {g["name"]: g["timezone"] for g in listed.json()}["188/53/A"] == "Mars/Olympus"

    res = client.patch(f"/groups/{company.id}", json={"timezone": "Asia/Jerusalem"},
                       headers=create_auth_header("u_master"))
    assert res.status_code == 200
    assert res.json()["timezone"] == "Asia/Jerusalem"


def test_a_refused_zone_is_not_echoed_beside_anything_else(client, db_session, mock_matrix_db):
    """The 422 names the field and the value sent for it, and nothing about
    the group: not its name, not the zone it has."""
    company = _group(db_session, "188/53/A")

    res = client.patch(f"/groups/{company.id}", json={"timezone": "Mars/Olympus"},
                       headers=create_auth_header("u_master"))

    assert res.status_code == 422
    [error] = res.json()["detail"]
    assert error["loc"] == ["body", "timezone"]
    assert "188/53/A" not in res.text
    assert clock.DEFAULT_TIMEZONE not in res.text
