"""The movement history's before state, reason and vocabulary. DATA-M8-1.

transaction_logs used to record who acted and, as one string, where the item
ended up. It now records the custody the item left and the one it went to --
holder, owner, group and location, from_ and to_ -- plus the reason the actor
gave, and its event_type holds the EventType vocabulary and nothing else.

Four layers, each tested on its own:

1. the routes: each custody event writes the right from_ and to_ values, and a
   reason is trimmed, blank-is-none and length-capped;
2. audit_trail.record_event: a custody event must be given its before state,
   and any other event must not;
3. the database: the CHECK and NOT NULL on event_type;
4. the revision b7e3d91c4f2a: it keeps legacy rows, refuses bad ones and
   reverses. Its idempotency is held, with the rest of the allowlist, by
   tests/test_group_schema.py.
"""
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from backend import audit_trail, models
from backend.enums import EventType
from backend.schemas import REASON_MAX
from tests.conftest import create_auth_header
from tests.test_group_schema import _downgrade, _upgrade

REVISION = "b7e3d91c4f2a"
PARENT_REVISION = "a8c2e5f19b47"

CUSTODY_COLUMNS = (
    "from_holder_user_id", "to_holder_user_id",
    "from_owner_user_id", "to_owner_user_id",
    "from_group_id", "to_group_id",
    "from_location", "to_location",
)
NON_CUSTODY_EVENTS = [e for e in EventType if e not in audit_trail.CUSTODY_EVENTS]


def _item(db_session, serial):
    return db_session.query(models.Equipment).filter_by(serial_number=serial).one()


def _last_log(db_session, item, event_type):
    return (
        db_session.query(models.TransactionLog)
        .filter_by(equipment_id=item.id, event_type=event_type.value)
        .order_by(models.TransactionLog.id.desc())
        .first()
    )


def _custody(log):
    return {column: getattr(log, column) for column in CUSTODY_COLUMNS}


def _transfer(client, who, **body):
    return client.post("/equipment/transfer", json=body, headers=create_auth_header(who))


def _assign(client, who, **body):
    return client.post("/equipment/assign_owner/", json=body, headers=create_auth_header(who))


def _classify(client, who, item_id, **body):
    return client.patch(
        f"/equipment/{item_id}/sensitivity", json=body, headers=create_auth_header(who)
    )


# --- 1. the routes -----------------------------------------------------------

def test_a_handover_to_a_person_records_both_ends(client, db_session, mock_matrix_db, group_graph):
    """Across companies, so the group moves as well as the holder.

    The battalion commander holds TRANSFER over both companies, which a move
    between them needs at both ends. The owner does not change on a handover,
    and the row says so rather than leaving it out.
    """
    item = _item(db_session, "SA100")
    soldier_a, soldier_b = mock_matrix_db["soldier_a"], mock_matrix_db["soldier_b"]

    res = _transfer(client, "u_bat_cmdr", equipment_id=item.id, to_holder_id=soldier_b.id)
    assert res.status_code == 200, res.text

    log = _last_log(db_session, item, EventType.HANDOVER)
    assert _custody(log) == {
        "from_holder_user_id": soldier_a.id,
        "to_holder_user_id": soldier_b.id,
        "from_owner_user_id": soldier_a.id,
        "to_owner_user_id": soldier_a.id,
        "from_group_id": group_graph["188/53/A"].id,
        "to_group_id": group_graph["188/53/B"].id,
        "from_location": None,
        "to_location": None,
    }
    # The actor is not the recipient, and the legacy column still names the
    # recipient for the movement report until DATA-M8-2.
    assert log.involved_user_id == mock_matrix_db["bat_cmdr"].id
    assert log.location == f"User:{soldier_b.full_name}"


def test_a_handover_to_a_location_and_back_records_the_place_at_each_end(
    client, db_session, mock_matrix_db, group_graph
):
    """Out to the armory, then back to a person: the place is the to_ value of
    the first row and the from_ value of the second. The group does not move,
    because an item at a location still belongs to its unit.
    """
    item = _item(db_session, "SA100")
    soldier_a = mock_matrix_db["soldier_a"]
    company_a = group_graph["188/53/A"].id

    res = _transfer(client, "u_cmdr_a", equipment_id=item.id, to_location="Armory")
    assert res.status_code == 200, res.text
    out = _last_log(db_session, item, EventType.HANDOVER_LOC)
    assert _custody(out) == {
        "from_holder_user_id": soldier_a.id,
        "to_holder_user_id": None,
        "from_owner_user_id": soldier_a.id,
        "to_owner_user_id": soldier_a.id,
        "from_group_id": company_a,
        "to_group_id": company_a,
        "from_location": None,
        "to_location": "Armory",
    }
    assert out.location == "Armory"

    res = _transfer(client, "u_cmdr_a", equipment_id=item.id, to_holder_id=soldier_a.id)
    assert res.status_code == 200, res.text
    back = _last_log(db_session, item, EventType.HANDOVER)
    assert back.from_location == "Armory"
    assert back.to_location is None
    assert back.from_holder_user_id is None
    assert back.to_holder_user_id == soldier_a.id


def test_an_assignment_records_owner_holder_and_group_at_both_ends(
    client, db_session, mock_matrix_db, group_graph
):
    """Assigning to someone in another company moves owner, holder and group."""
    item = _item(db_session, "SA100")
    soldier_a, soldier_b = mock_matrix_db["soldier_a"], mock_matrix_db["soldier_b"]

    res = _assign(client, "u_bat_cmdr", equipment_id=item.id, owner_id=soldier_b.id)
    assert res.status_code == 200, res.text

    log = _last_log(db_session, item, EventType.ASSIGN)
    assert _custody(log) == {
        "from_holder_user_id": soldier_a.id,
        "to_holder_user_id": soldier_b.id,
        "from_owner_user_id": soldier_a.id,
        "to_owner_user_id": soldier_b.id,
        "from_group_id": group_graph["188/53/A"].id,
        "to_group_id": group_graph["188/53/B"].id,
        "from_location": None,
        "to_location": None,
    }


def test_an_assignment_from_a_location_records_the_place_it_left(
    client, db_session, mock_matrix_db
):
    """assign_owner clears custom_location; the from_ side must still have it."""
    item = _item(db_session, "SA100")
    assert _transfer(
        client, "u_cmdr_a", equipment_id=item.id, to_location="Store 2"
    ).status_code == 200

    res = _assign(client, "u_cmdr_a", equipment_id=item.id, owner_id=mock_matrix_db["company_tech_a"].id)
    assert res.status_code == 200, res.text

    log = _last_log(db_session, item, EventType.ASSIGN)
    assert log.from_location == "Store 2"
    assert log.to_location is None
    assert log.from_holder_user_id is None


def test_a_reclassification_and_a_fault_record_no_custody(client, db_session, mock_matrix_db):
    """Through the routes, for the two non-custody events most like a move: a
    decision about the item, and a report on it. Neither moves anybody."""
    item = _item(db_session, "SA100")

    assert _classify(client, "u_master", item.id, sensitivity="CLASSIFIED").status_code == 200
    res = client.post(
        "/maintenance/report",
        json={"equipment_id": item.id, "fault_name": "Cracked Housing", "description": "x"},
        headers=create_auth_header("u_soldier_a"),
    )
    assert res.status_code == 200, res.text

    for event_type in (EventType.RECLASSIFY, EventType.FAULT):
        log = _last_log(db_session, item, event_type)
        assert log is not None, f"no {event_type.value} row"
        assert _custody(log) == dict.fromkeys(CUSTODY_COLUMNS), event_type
        assert log.location is None


REASON_ROUTES = {
    "handover": (EventType.HANDOVER, lambda client, item, users, reason: _transfer(
        client, "u_cmdr_a", equipment_id=item.id, to_holder_id=users["company_tech_a"].id, reason=reason
    )),
    "handover_loc": (EventType.HANDOVER_LOC, lambda client, item, users, reason: _transfer(
        client, "u_cmdr_a", equipment_id=item.id, to_location="Armory", reason=reason
    )),
    "assign": (EventType.ASSIGN, lambda client, item, users, reason: _assign(
        client, "u_cmdr_a", equipment_id=item.id, owner_id=users["company_tech_a"].id, reason=reason
    )),
    "reclassify": (EventType.RECLASSIFY, lambda client, item, users, reason: _classify(
        client, "u_master", item.id, sensitivity="CLASSIFIED", reason=reason
    )),
}


@pytest.mark.parametrize("route", sorted(REASON_ROUTES))
@pytest.mark.parametrize(
    ("sent", "stored"),
    [
        ("  handed in for repair  ", "handed in for repair"),
        ("   ", None),
        ("", None),
        (None, None),
        ("x" * REASON_MAX, "x" * REASON_MAX),
    ],
    ids=["trimmed", "blank", "empty", "absent", "at-the-cap"],
)
def test_the_reason_is_stored_trimmed_and_a_blank_one_is_none(
    client, db_session, mock_matrix_db, route, sent, stored
):
    event_type, call = REASON_ROUTES[route]
    item = _item(db_session, "SA100")

    res = call(client, item, mock_matrix_db, sent)
    assert res.status_code == 200, res.text
    assert _last_log(db_session, item, event_type).reason == stored


@pytest.mark.parametrize("route", sorted(REASON_ROUTES))
def test_a_reason_over_the_cap_is_a_422_that_writes_nothing(
    client, db_session, mock_matrix_db, route
):
    """The cap is on the trimmed text, so padding cannot hide a long reason
    and a long one padded with spaces is measured without them."""
    _event_type, call = REASON_ROUTES[route]
    item = _item(db_session, "SA100")
    custody_before = audit_trail.custody_of(item)
    sensitivity_before = item.sensitivity
    logs_before = db_session.query(models.TransactionLog).count()

    res = call(client, item, mock_matrix_db, "  " + "x" * (REASON_MAX + 1) + "  ")
    assert res.status_code == 422, res.text

    db_session.expire_all()
    assert db_session.query(models.TransactionLog).count() == logs_before
    assert audit_trail.custody_of(item) == custody_before
    assert item.sensitivity == sensitivity_before


def test_a_long_reason_padded_with_spaces_is_measured_without_them(
    client, db_session, mock_matrix_db
):
    item = _item(db_session, "SA100")
    res = _transfer(
        client, "u_cmdr_a", equipment_id=item.id, to_location="Armory",
        reason="   " + "x" * REASON_MAX + "   ",
    )
    assert res.status_code == 200, res.text
    assert _last_log(db_session, item, EventType.HANDOVER_LOC).reason == "x" * REASON_MAX


@pytest.mark.parametrize("sent", [42, ["a"], {"text": "a"}, True], ids=repr)
def test_a_reason_that_is_not_text_is_a_422(client, db_session, mock_matrix_db, sent):
    """Pydantic does not coerce a number or a list into a str, so a client
    sending one is told so rather than having "42" stored for it."""
    item = _item(db_session, "SA100")
    logs_before = db_session.query(models.TransactionLog).count()

    res = _transfer(client, "u_cmdr_a", equipment_id=item.id, to_location="Armory", reason=sent)

    assert res.status_code == 422, res.text
    assert db_session.query(models.TransactionLog).count() == logs_before


def test_a_hebrew_reason_with_a_line_break_survives_as_written(client, db_session, mock_matrix_db):
    """Only the ends are trimmed: inner whitespace, a newline and RTL text are
    the actor's words and are kept."""
    item = _item(db_session, "SA100")
    reason = "הועבר לנשקייה\nלבדיקה שבועית"

    res = _transfer(client, "u_cmdr_a", equipment_id=item.id, to_location="נשקייה", reason=f"\t{reason} ")
    assert res.status_code == 200, res.text

    log = _last_log(db_session, item, EventType.HANDOVER_LOC)
    assert log.reason == reason
    assert log.to_location == "נשקייה"


def test_a_handover_to_the_current_holder_records_that_nothing_moved(
    client, db_session, mock_matrix_db
):
    """The route allows it and logs it, as before DATA-M8-1. What changes is
    that the row now says so: from_ and to_ are the same."""
    item = _item(db_session, "SA100")
    soldier_a = mock_matrix_db["soldier_a"]

    res = _transfer(client, "u_cmdr_a", equipment_id=item.id, to_holder_id=soldier_a.id)
    assert res.status_code == 200, res.text

    log = _last_log(db_session, item, EventType.HANDOVER)
    assert log.from_holder_user_id == log.to_holder_user_id == soldier_a.id
    assert log.from_group_id == log.to_group_id


def test_a_refused_transfer_with_a_reason_writes_nothing(client, db_session, mock_matrix_db):
    """The reason is validated before the route runs, and the gates still run
    before the write: a soldier holds no TRANSFER, so a well-formed reason
    changes nothing about the 403."""
    item = _item(db_session, "SA100")
    logs_before = db_session.query(models.TransactionLog).count()

    res = _transfer(client, "u_soldier_a", equipment_id=item.id, to_location="Armory", reason="mine")

    assert res.status_code == 403, res.text
    assert db_session.query(models.TransactionLog).count() == logs_before


# --- 2. record_event ---------------------------------------------------------

@pytest.mark.parametrize("event_type", sorted(audit_trail.CUSTODY_EVENTS))
def test_a_custody_event_without_its_before_state_is_refused(db_session, mock_matrix_db, event_type):
    item = _item(db_session, "SA100")
    logs_before = db_session.query(models.TransactionLog).count()

    with pytest.raises(ValueError, match="needs the custody before"):
        audit_trail.record_event(
            db_session, equipment=item, actor=mock_matrix_db["master"], event_type=event_type
        )

    assert db_session.query(models.TransactionLog).count() == logs_before


@pytest.mark.parametrize("event_type", NON_CUSTODY_EVENTS)
def test_any_other_event_with_a_before_state_is_refused(db_session, mock_matrix_db, event_type):
    item = _item(db_session, "SA100")
    logs_before = db_session.query(models.TransactionLog).count()

    with pytest.raises(ValueError, match="moves no custody"):
        audit_trail.record_event(
            db_session,
            equipment=item,
            actor=mock_matrix_db["master"],
            event_type=event_type,
            before=audit_trail.custody_of(item),
        )

    assert db_session.query(models.TransactionLog).count() == logs_before


@pytest.mark.parametrize("event_type", NON_CUSTODY_EVENTS)
def test_any_other_event_leaves_every_custody_column_empty(db_session, mock_matrix_db, event_type):
    item = _item(db_session, "SA100")

    audit_trail.record_event(
        db_session, equipment=item, actor=mock_matrix_db["master"], event_type=event_type
    )
    db_session.commit()

    log = _last_log(db_session, item, event_type)
    assert _custody(log) == dict.fromkeys(CUSTODY_COLUMNS)
    assert log.location is None


def test_the_custody_events_are_exactly_the_three_moves():
    """Pinned, so a fourth custody event is a decision and not a slip."""
    assert audit_trail.CUSTODY_EVENTS == {
        EventType.HANDOVER, EventType.HANDOVER_LOC, EventType.ASSIGN,
    }


def test_the_before_state_is_the_one_passed_in_and_the_after_state_is_the_item(
    db_session, mock_matrix_db, group_graph
):
    """record_event reads the after state off the item when it is called, so
    the caller's order matters: snapshot, change, then record."""
    item = _item(db_session, "SA100")
    before = audit_trail.custody_of(item)
    item.holder_user_id = None
    item.custom_location = "Vault"

    audit_trail.record_event(
        db_session,
        equipment=item,
        actor=mock_matrix_db["master"],
        event_type=EventType.HANDOVER_LOC,
        before=before,
    )
    db_session.commit()

    log = _last_log(db_session, item, EventType.HANDOVER_LOC)
    assert (log.from_holder_user_id, log.to_holder_user_id) == (mock_matrix_db["soldier_a"].id, None)
    assert (log.from_location, log.to_location) == (None, "Vault")
    assert log.location == "Vault"


def test_an_unnamed_recipient_is_spelled_unknown(db_session, mock_matrix_db):
    """full_name is nullable; the legacy string must not read "User:None"."""
    item = _item(db_session, "SA100")
    recipient = mock_matrix_db["soldier_b"]
    recipient.full_name = None
    db_session.commit()

    before = audit_trail.custody_of(item)
    item.holder_user_id = recipient.id
    audit_trail.record_event(
        db_session,
        equipment=item,
        actor=mock_matrix_db["master"],
        event_type=EventType.HANDOVER,
        before=before,
    )
    db_session.commit()

    assert _last_log(db_session, item, EventType.HANDOVER).location == "User:Unknown"


# --- 3. the database ---------------------------------------------------------

@pytest.mark.parametrize("value", ["TRANSFER", "handover", "", None], ids=repr)
def test_the_database_refuses_an_event_type_outside_the_vocabulary(db_session, mock_matrix_db, value):
    """Raw SQL, so neither the enum nor record_event is in the way. NULL is
    refused by NOT NULL, the rest by the CHECK -- including the lowercase
    spelling of a real member."""
    item = _item(db_session, "SA100")
    with pytest.raises(IntegrityError):
        db_session.execute(
            text("INSERT INTO transaction_logs (equipment_id, event_type) VALUES (:id, :et)"),
            {"id": item.id, "et": value},
        )
        db_session.flush()
    db_session.rollback()


@pytest.mark.parametrize("event_type", list(EventType))
def test_the_database_accepts_every_member(db_session, mock_matrix_db, event_type):
    item = _item(db_session, "SA100")
    db_session.execute(
        text("INSERT INTO transaction_logs (equipment_id, event_type) VALUES (:id, :et)"),
        {"id": item.id, "et": event_type.value},
    )
    db_session.commit()


def test_the_custody_keys_refuse_a_dangling_reference(db_session, mock_matrix_db):
    """The keys exist and are enforced on the application's SQLite engine.

    A dangling reference is refused by a key with any rule, or none, so this
    does not pin RESTRICT; the schema comparison in test_group_schema.py and
    the Postgres test of the revision do.
    """
    item = _item(db_session, "SA100")
    for column in ("from_holder_user_id", "to_owner_user_id", "to_group_id"):
        with pytest.raises(IntegrityError):
            db_session.execute(
                text(
                    f"INSERT INTO transaction_logs (equipment_id, event_type, {column}) "
                    "VALUES (:id, 'HANDOVER', 999999)"
                ),
                {"id": item.id},
            )
            db_session.flush()
        db_session.rollback()


# --- 4. the revision ---------------------------------------------------------

def _seed_parent(conn, event_types=("HANDOVER",)):
    """A database at PARENT_REVISION with one item and one log row per value.

    The rows carry what a pre-DATA-M8-1 writer left: an actor, the after state
    as a string, and an involved_location_id that nothing ever wrote but that a
    hand-made row could hold.
    """
    conn.exec_driver_sql("INSERT INTO groups (id, name, kind) VALUES (1, '188', 'UNIT')")
    conn.exec_driver_sql("INSERT INTO catalog_items (id, name) VALUES (1, 'M4')")
    conn.exec_driver_sql("INSERT INTO locations (id, name) VALUES (1, 'Armory')")
    conn.exec_driver_sql("INSERT INTO users (id, personal_number, full_name) VALUES (1, 'u1', 'One')")
    conn.exec_driver_sql(
        "INSERT INTO equipment (id, serial_number, catalog_item_id, group_id, status) "
        "VALUES (1, 'SN-1', 1, 1, 'Functional')"
    )
    for i, event_type in enumerate(event_types, start=1):
        conn.execute(
            text(
                "INSERT INTO transaction_logs "
                "(id, equipment_id, involved_user_id, involved_location_id, event_type, location) "
                "VALUES (:id, 1, 1, 1, :et, 'User:One')"
            ),
            {"id": i, "et": event_type},
        )


def _columns(engine, table="transaction_logs"):
    return {c["name"]: c for c in inspect(engine).get_columns(table)}


def _version(engine):
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def test_a_legacy_row_survives_with_no_before_state(tmp_path):
    """No backfill: the old string stays, and every new column is NULL."""
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    _upgrade(engine, PARENT_REVISION)
    with engine.begin() as conn:
        _seed_parent(conn, ("HANDOVER", "ASSIGN"))

    _upgrade(engine, REVISION)

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, equipment_id, involved_user_id, event_type, location, reason, "
            + ", ".join(CUSTODY_COLUMNS)
            + " FROM transaction_logs ORDER BY id"
        )).all()
    columns = _columns(engine)
    engine.dispose()

    assert [tuple(r[:6]) for r in rows] == [
        (1, 1, 1, "HANDOVER", "User:One", None),
        (2, 1, 1, "ASSIGN", "User:One", None),
    ]
    assert all(value is None for r in rows for value in r[6:])
    assert "involved_location_id" not in columns
    assert columns["event_type"]["nullable"] is False


@pytest.mark.parametrize(
    "bad",
    [None, "TRANSFER", "handover", ""],
    ids=["null", "unknown", "lowercase", "empty"],
)
def test_the_upgrade_refuses_a_bad_event_type_and_names_every_row(tmp_path, bad):
    """One good row and two bad ones: both bad ids are named, and nothing has
    moved -- the version, the dead column and the nullability are as they
    were."""
    engine = create_engine(f"sqlite:///{tmp_path / 'bad.db'}")
    _upgrade(engine, PARENT_REVISION)
    with engine.begin() as conn:
        _seed_parent(conn, ("HANDOVER", bad, bad))

    with pytest.raises(RuntimeError) as refused:
        _upgrade(engine, REVISION)

    message = str(refused.value)
    assert "2 transaction_logs row(s)" in message
    assert "#2 holds" in message and "#3 holds" in message
    assert "#1 holds" not in message
    assert "Nothing has been changed" in message

    columns = _columns(engine)
    assert _version(engine) == PARENT_REVISION
    assert "involved_location_id" in columns
    assert "reason" not in columns
    assert columns["event_type"]["nullable"] is True
    engine.dispose()


def test_the_v4_spellings_become_their_event_types(tmp_path):
    """The root main.py of the first Docker image wrote 'FAULT_REPORT' and
    'REPAIR'. They are FAULT and FIX under other names, so the upgrade renames
    them and touches nothing else about those rows."""
    engine = create_engine(f"sqlite:///{tmp_path / 'v4.db'}")
    _upgrade(engine, PARENT_REVISION)
    with engine.begin() as conn:
        _seed_parent(conn, ("FAULT_REPORT", "REPAIR", "HANDOVER"))

    _upgrade(engine, REVISION)

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, event_type, location, involved_user_id FROM transaction_logs ORDER BY id"
        )).all()
    engine.dispose()

    assert rows == [
        (1, "FAULT", "User:One", 1),
        (2, "FIX", "User:One", 1),
        (3, "HANDOVER", "User:One", 1),
    ]


def test_a_refusal_renames_nothing_either(tmp_path):
    """Beside a row that is refused, an old spelling keeps its old value, and
    only the refused row is named.

    The outcome, not the order: the revision checks before it renames, but a
    rename run first would be rolled back with the refused transaction, and
    this test cannot tell the two apart (measured with a mutant).
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'mixed.db'}")
    _upgrade(engine, PARENT_REVISION)
    with engine.begin() as conn:
        _seed_parent(conn, ("FAULT_REPORT", "TRANSFER"))

    with pytest.raises(RuntimeError) as refused:
        _upgrade(engine, REVISION)

    assert "1 transaction_logs row(s)" in str(refused.value)
    assert "#2 holds 'TRANSFER'" in str(refused.value)
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT event_type FROM transaction_logs ORDER BY id"
        )).scalars().all() == ["FAULT_REPORT", "TRANSFER"]
    engine.dispose()


def test_the_downgrade_restores_the_dead_column_and_the_upgrade_runs_again(tmp_path):
    """involved_location_id comes back nullable, with its RESTRICT key and its
    index; the new columns, the CHECK and NOT NULL go. Then up again."""
    engine = create_engine(f"sqlite:///{tmp_path / 'down.db'}")
    _upgrade(engine, PARENT_REVISION)
    with engine.begin() as conn:
        _seed_parent(conn)

    _upgrade(engine, REVISION)
    _downgrade(engine, PARENT_REVISION)

    insp = inspect(engine)
    columns = _columns(engine)
    keys = {fk["name"]: fk for fk in insp.get_foreign_keys("transaction_logs")}
    indexes = {ix["name"] for ix in insp.get_indexes("transaction_logs")}
    checks = {c.get("name") for c in insp.get_check_constraints("transaction_logs")}

    assert columns["involved_location_id"]["nullable"] is True
    dead = keys["fk_transaction_logs_involved_location_id"]
    assert dead["referred_table"] == "locations"
    assert dead["options"].get("ondelete") == "RESTRICT"
    assert "ix_transaction_logs_involved_location_id" in indexes
    assert set(CUSTODY_COLUMNS + ("reason",)).isdisjoint(columns)
    assert "ck_transaction_logs_event_type" not in checks
    assert columns["event_type"]["nullable"] is True
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT id, event_type, location, involved_location_id FROM transaction_logs"
        )).all() == [(1, "HANDOVER", "User:One", None)]

    _upgrade(engine, REVISION)
    assert "reason" in _columns(engine)
    engine.dispose()
