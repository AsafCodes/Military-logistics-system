"""DATA-M8-2. /reports/daily_movement says who moved what, from where to where, and why.

The route sent `"reporter_name": None` on every row and the destination as
one `location` string, with no response model; the frontend declared a
private copy of the row and never showed the reporter. DATA-M8-1 gave
transaction_logs the columns, and this is the read side:

- the rows: the reporter is the actor, the from_ and to_ names are the ones
  each event recorded, a new row leaves `location` empty and a legacy row
  keeps it;
- the contract: schemas.MovementReportItem and MovementReportItem in
  frontend/src/types/index.ts, key for key, both ways, with
  test_report_item_contract.py's parser and its rules;
- the query count, which must not grow with the rows.
"""
from datetime import timedelta

import pytest

from backend import authz, clock, models
from backend.enums import EventType
from tests.conftest import create_auth_header
from tests.test_movement_history import REASON_ROUTES
from tests.test_report_item_contract import (
    FRONTEND_SRC,
    SCALARS,
    TYPES_FILE,
    declared_fields,
)

INTERFACE = "MovementReportItem"
URL = "/reports/daily_movement"

CUSTODY_NAME_KEYS = (
    "from_holder_name", "to_holder_name",
    "from_owner_name", "to_owner_name",
    "from_group_name", "to_group_name",
    "from_location", "to_location",
)


def _item(db_session, serial):
    return db_session.query(models.Equipment).filter_by(serial_number=serial).one()


def _post(client, who, url, body):
    res = client.post(url, json=body, headers=create_auth_header(who))
    assert res.status_code == 200, res.text


def _report(client, who="u_master"):
    res = client.get(URL, headers=create_auth_header(who))
    assert res.status_code == 200, res.text
    return res.json()


@pytest.fixture
def movements(client, db_session, mock_matrix_db, group_graph):
    """One row of every shape the report can send, keyed by what it is.

    Between them they put every nullable key both null and set, which the
    contract test below needs:

    - legacy: shaped like a row from before DATA-M8-1, `location` and no
      custody columns; it also has no actor, which is what puts
      reporter_name's null on the wire (a real old row has one);
    - from_shelf: an item with no serial, no holder and no owner, sitting at a
      place, handed to a person with a reason;
    - to_armory: a handover to a place;
    - assign: an assignment into the other company, so the group moves;
    - fault: a non-custody event.
    """
    users = mock_matrix_db
    template = _item(db_session, "SA100")

    legacy = models.TransactionLog(
        equipment_id=template.id,
        event_type=EventType.HANDOVER.value,
        timestamp=clock.utcnow() - timedelta(hours=1),
        location="User:Someone Old",
    )
    shelf_item = models.Equipment(
        catalog_item_id=template.catalog_item_id,
        group_id=template.group_id,
        status="Functional",
        custom_location="Shelf 3",
    )
    db_session.add_all([legacy, shelf_item])
    db_session.commit()
    legacy_id, shelf_id = legacy.id, shelf_item.id

    _post(client, "u_bat_cmdr", "/equipment/transfer", {
        "equipment_id": shelf_id, "to_holder_id": users["soldier_a"].id, "reason": " issued for the exercise ",
    })
    _post(client, "u_bat_cmdr", "/equipment/transfer", {
        "equipment_id": _item(db_session, "SB200").id, "to_location": "Armory",
    })
    _post(client, "u_bat_cmdr", "/equipment/assign_owner/", {
        "equipment_id": _item(db_session, "TA300").id, "owner_id": users["soldier_b"].id,
    })
    _post(client, "u_soldier_a", "/maintenance/report", {
        "equipment_id": template.id, "fault_name": "Cracked Housing", "description": "x",
    })

    rows = _report(client)
    by_shape = {"legacy": [r for r in rows if r["id"] == legacy_id]}
    for name, event_type, serial in [
        ("from_shelf", "HANDOVER", None),
        ("to_armory", "HANDOVER_LOC", "SB200"),
        ("assign", "ASSIGN", "TA300"),
        ("fault", "FAULT", "SA100"),
    ]:
        by_shape[name] = [
            r for r in rows if r["event_type"] == event_type and r["serial_number"] == serial
        ]
    for name, found in by_shape.items():
        assert len(found) == 1, f"expected one {name} row, got {found}"
    return rows, {name: found[0] for name, found in by_shape.items()}


# --- the rows ----------------------------------------------------------------

def test_a_handover_from_a_place_names_both_ends_the_reporter_and_the_reason(
    movements, mock_matrix_db
):
    _, row = movements
    row = row["from_shelf"]
    assert row["reporter_name"] == mock_matrix_db["bat_cmdr"].full_name
    assert (row["from_holder_name"], row["to_holder_name"]) == (None, mock_matrix_db["soldier_a"].full_name)
    assert (row["from_location"], row["to_location"]) == ("Shelf 3", None)
    assert (row["from_owner_name"], row["to_owner_name"]) == (None, None)
    assert row["from_group_name"] == row["to_group_name"] == "188/53/A"
    assert row["reason"] == "issued for the exercise"
    assert row["location"] is None


def test_a_handover_to_a_place_names_the_holder_it_left_and_the_place(movements, mock_matrix_db):
    _, row = movements
    row = row["to_armory"]
    soldier_b = mock_matrix_db["soldier_b"].full_name
    assert (row["from_holder_name"], row["to_holder_name"]) == (soldier_b, None)
    assert (row["from_location"], row["to_location"]) == (None, "Armory")
    assert row["from_owner_name"] == row["to_owner_name"] == soldier_b
    assert row["from_group_name"] == row["to_group_name"] == "188/53/B"
    assert row["reason"] is None
    assert row["location"] is None


def test_an_assignment_into_another_company_names_both_owners_and_both_units(
    movements, mock_matrix_db
):
    _, row = movements
    row = row["assign"]
    tech_a, soldier_b = mock_matrix_db["company_tech_a"].full_name, mock_matrix_db["soldier_b"].full_name
    assert (row["from_owner_name"], row["to_owner_name"]) == (tech_a, soldier_b)
    assert (row["from_holder_name"], row["to_holder_name"]) == (tech_a, soldier_b)
    assert (row["from_group_name"], row["to_group_name"]) == ("188/53/A", "188/53/B")
    assert row["reporter_name"] == mock_matrix_db["bat_cmdr"].full_name


def test_a_fault_names_its_reporter_and_no_move(movements, mock_matrix_db):
    _, row = movements
    row = row["fault"]
    assert row["reporter_name"] == mock_matrix_db["soldier_a"].full_name
    assert {key: row[key] for key in CUSTODY_NAME_KEYS} == dict.fromkeys(CUSTODY_NAME_KEYS)
    assert row["location"] is None


def test_a_legacy_row_keeps_its_location_and_has_no_custody_names(movements):
    _, row = movements
    row = row["legacy"]
    assert row["location"] == "User:Someone Old"
    assert row["reporter_name"] is None
    assert {key: row[key] for key in CUSTODY_NAME_KEYS} == dict.fromkeys(CUSTODY_NAME_KEYS)


def test_a_person_with_no_name_is_sent_as_null_not_coalesced(
    client, db_session, mock_matrix_db
):
    """full_name is nullable (DATA-L11). The report sends what the column
    holds; inventing a name for an audit row would be the report lying."""
    item = _item(db_session, "SA100")
    recipient = mock_matrix_db["soldier_b"]
    recipient.full_name = None
    db_session.commit()

    _post(client, "u_bat_cmdr", "/equipment/transfer", {"equipment_id": item.id, "to_holder_id": recipient.id})

    [row] = [r for r in _report(client) if r["event_type"] == "HANDOVER"]
    assert row["to_holder_name"] is None
    assert row["from_holder_name"] == mock_matrix_db["soldier_a"].full_name


def test_the_timestamp_is_utc_with_a_z(movements):
    """The response model writes the timestamp now, not clock.iso_z; the
    wire format must not change with it."""
    rows, _ = movements
    assert rows and all(row["timestamp"].endswith("Z") for row in rows), rows


# --- the contract ------------------------------------------------------------

def test_the_shared_type_declares_exactly_the_keys_every_row_carries(movements):
    fields = declared_fields(TYPES_FILE.read_text(encoding="utf-8"), INTERFACE)

    optional = sorted(key for key, (is_optional, _) in fields.items() if is_optional)
    assert optional == [], f"{INTERFACE} marks {optional} optional, but the route sends every key"

    rows, _ = movements
    for row in rows:
        assert set(row) == set(fields), (
            f"route sends {sorted(set(row) - set(fields))} undeclared; "
            f"type declares {sorted(set(fields) - set(row))} never sent"
        )


def test_null_appears_on_the_wire_exactly_where_the_type_admits_it(movements):
    fields = declared_fields(TYPES_FILE.read_text(encoding="utf-8"), INTERFACE)
    rows, _ = movements

    for row in rows:
        for key, value in row.items():
            _, alternatives = fields[key]
            if value is None:
                assert "null" in alternatives, (
                    f"route sent null for {key}, which {INTERFACE} types as {' | '.join(alternatives)}"
                )
                continue
            scalars = [alt for alt in alternatives if alt != "null"]
            assert len(scalars) == 1 and scalars[0] in SCALARS, (
                f"{INTERFACE}.{key} is {' | '.join(alternatives)}; this test reads one scalar"
            )
            # bool is an int subclass; a number field must not accept one.
            assert type(value) is SCALARS[scalars[0]], (
                f"{key}={value!r} on the wire, {INTERFACE} says {scalars[0]}"
            )

    for key, (_, alternatives) in fields.items():
        assert any(row[key] is not None for row in rows), (
            f"{key} is null on every row, so its declared type was never compared"
        )
        if "null" in alternatives:
            assert any(row[key] is None for row in rows), (
                f"{INTERFACE}.{key} admits null, but no row sends it"
            )


def test_the_response_model_and_the_type_agree_on_every_key_and_every_null():
    """The two tests above compare the type with what the fixture SENDS, so a
    key the response model declares nullable and the route never sends null
    passes them unseen -- `timestamp: Optional[datetime]` would. This compares
    the type with the response model itself, through the OpenAPI document
    FastAPI builds from it."""
    from backend.main import app

    spec = app.openapi()
    body = spec["paths"][URL]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert body.get("type") == "array", body
    assert body["items"] == {"$ref": f"#/components/schemas/{INTERFACE}"}, body

    model = spec["components"]["schemas"][INTERFACE]
    fields = declared_fields(TYPES_FILE.read_text(encoding="utf-8"), INTERFACE)
    assert set(model["properties"]) == set(fields)
    assert set(model["required"]) == set(fields), "a key with a default can be left out of a row"

    for key, (_, alternatives) in fields.items():
        prop = model["properties"][key]
        schema_nullable = any(alt.get("type") == "null" for alt in prop.get("anyOf", []))
        assert schema_nullable == ("null" in alternatives), (
            f"{key}: the response model says nullable={schema_nullable}, "
            f"{INTERFACE} says {' | '.join(alternatives)}"
        )


def test_only_the_equipment_relationship_on_a_log_row_can_write():
    """The seven name relationships are viewonly, and SQLAlchemy writes
    nothing for an assignment to a viewonly relationship, so `log.to_holder =
    someone` changes no row and audit_trail.record_event's ids stay the one way
    into those columns. This checks the flag, not a flush."""
    relationships = models.TransactionLog.__mapper__.relationships
    names = {"actor", "from_holder", "to_holder", "from_owner", "to_owner", "from_group", "to_group"}
    assert names <= set(relationships.keys())
    assert {key for key, rel in relationships.items() if not rel.viewonly} == {"equipment"}


@pytest.mark.parametrize("route", sorted(REASON_ROUTES))
def test_the_reason_reaches_the_report_from_every_route_that_takes_one(
    client, db_session, mock_matrix_db, route
):
    event_type, call = REASON_ROUTES[route]
    res = call(client, _item(db_session, "SA100"), mock_matrix_db, "  for the record ")
    assert res.status_code == 200, res.text

    [row] = [r for r in _report(client) if r["event_type"] == event_type.value]
    assert row["reason"] == "for the record"


def test_a_viewer_who_cannot_see_the_item_gets_none_of_its_names(
    client, db_session, mock_matrix_db
):
    """The names are new on the wire, so the scoping that decides whose rows
    a viewer gets is asserted here again: Company B's commander sees nothing
    of a move inside Company A."""
    item = _item(db_session, "SA100")
    _post(client, "u_cmdr_a", "/equipment/transfer", {
        "equipment_id": item.id, "to_holder_id": mock_matrix_db["company_tech_a"].id, "reason": "inside A",
    })

    assert [r for r in _report(client, "u_cmdr_a") if r["serial_number"] == "SA100"]
    assert [r for r in _report(client, "u_cmdr_b") if r["serial_number"] == "SA100"] == []


def test_no_second_definition_of_a_movement_row_exists_in_the_frontend():
    """DailyActivityTable declared its own copy of the row until DATA-M8-2.

    Recognised by two keys no other payload carries. Same scan, and the same
    blind spots, as test_report_item_contract's uniqueness test: a flat body
    only, comments included.
    """
    import re

    declaration = re.compile(
        r"(?:interface\s+(?P<iface>\w+)[^{]*|type\s+(?P<alias>\w+)\s*=\s*)\{(?P<body>[^{}]*)\}"
    )
    found = []
    for path in sorted(FRONTEND_SRC.rglob("*.ts*")):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        for match in declaration.finditer(path.read_text(encoding="utf-8")):
            body = match["body"]
            if re.search(r"\bto_holder_name\b", body) and re.search(r"\bfrom_location\b", body):
                found.append((path.relative_to(FRONTEND_SRC).as_posix(), match["iface"] or match["alias"]))

    assert found == [("types/index.ts", INTERFACE)], found


# --- the query count ---------------------------------------------------------

def _stock_moves(db_session, n, *, tag):
    """n rows, each on its own item, naming its own five people and two units.

    Distinct per row -- item, people AND units -- because a lazy load of a row
    already in the identity map is one query the first time and none after:
    rows sharing one cost the same at 2 rows and at 10, and hide a dropped
    load. That is how a missing to_group load, and then a missing equipment
    load, first got past this test.
    """
    template = _item(db_session, "SA100")
    items = [
        models.Equipment(
            catalog_item_id=template.catalog_item_id, group_id=template.group_id,
            status="Functional", serial_number=f"{tag}-{i}",
        )
        for i in range(n)
    ]
    people = [
        models.User(personal_number=f"{tag}_{i}_{role}", full_name=f"{tag} {i} {role}")
        for i in range(n)
        for role in ("actor", "from_h", "to_h", "from_o", "to_o")
    ]
    units = [authz.Unit(name=f"{tag} {i} {end}", timezone=clock.DEFAULT_TIMEZONE) for i in range(n) for end in ("from", "to")]
    db_session.add_all(items + people + units)
    db_session.flush()
    for i in range(n):
        actor, from_h, to_h, from_o, to_o = people[i * 5:i * 5 + 5]
        a, b = units[i * 2].id, units[i * 2 + 1].id
        db_session.add(models.TransactionLog(
            equipment_id=items[i].id,
            involved_user_id=actor.id,
            event_type=EventType.ASSIGN.value,
            timestamp=clock.utcnow() - timedelta(minutes=i),
            from_holder_user_id=from_h.id, to_holder_user_id=to_h.id,
            from_owner_user_id=from_o.id, to_owner_user_id=to_o.id,
            from_group_id=a, to_group_id=b,
        ))
    db_session.commit()


def test_the_report_does_not_fan_out(client, db_session, mock_matrix_db, count_queries):
    token = create_auth_header("u_master")

    _stock_moves(db_session, 2, tag="small")
    with count_queries() as small:
        assert len(client.get(URL, headers=token).json()) == 2
    _stock_moves(db_session, 8, tag="large")
    with count_queries() as large:
        assert len(client.get(URL, headers=token).json()) == 10

    assert large.count == small.count, (
        f"{small.count} statements for 2 rows, {large.count} for 10 "
        f"({len(large.against('FROM users'))} of them read users)"
    )
