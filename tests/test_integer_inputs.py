"""Every integer a request carries is bounded to the range of the columns it
is compared with.

The defect: no integer input was bounded, so any number reached the
database. Python's sqlite3 cannot bind an integer outside 64 bits and raises
OverflowError, so on SQLite each of the routes below answered 500 for an id
such as 99999999999999999999. Postgres compares an `integer` column against
a larger value without error and answered 404 -- the same request, two
answers. Measured on both dialects before the fix, every input below.

The fix is schemas.DbInt: the range of a Postgres `integer`, which is what
an `Integer` column is there, so no stored id can lie outside it. A value
outside it is refused with 422, as a non-number is, before any query runs.

Two layers, because each catches what the other cannot:
- the OpenAPI scan finds every integer input the application declares, so
  a new route or field with a bare `int` fails here without anyone adding
  a case;
- the requests below prove the bound is what refuses the value, input by
  input, and the scan holds the list of them complete.
"""
import pytest

from backend import authz, models
from backend.main import app
from backend.schemas import DB_INT_MAX, DB_INT_MIN
from tests.conftest import create_auth_header

TOO_BIG = [DB_INT_MAX + 1, 2**63, 10**20]
TOO_SMALL = [DB_INT_MIN - 1, -(2**63) - 1]


def _ints_in(schema, components, seen=()):
    """(dotted name, integer schema) for every integer reachable in `schema`."""
    if "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        if name in seen:
            return []
        return _ints_in(components[name], components, seen + (name,))
    found = []
    for key in ("anyOf", "oneOf", "allOf"):
        for sub in schema.get(key, []):
            found += _ints_in(sub, components, seen)
    if schema.get("type") == "integer":
        found.append(("", schema))
    for prop, sub in schema.get("properties", {}).items():
        found += [(prop + ("." + n if n else ""), s) for n, s in _ints_in(sub, components, seen)]
    if "items" in schema:
        found += _ints_in(schema["items"], components, seen)
    return found


def declared_integer_inputs():
    """{(METHOD, path, where, name): schema} for every integer a request carries."""
    spec = app.openapi()
    components = spec.get("components", {}).get("schemas", {})
    inputs = {}
    for path, operations in spec["paths"].items():
        for method, op in operations.items():
            for param in op.get("parameters", []):
                for _, schema in _ints_in(param["schema"], components):
                    inputs[(method.upper(), path, param["in"], param["name"])] = schema
            for content in op.get("requestBody", {}).get("content", {}).values():
                for name, schema in _ints_in(content["schema"], components):
                    inputs[(method.upper(), path, "body", name)] = schema
    return inputs


def test_every_integer_input_is_bounded_to_the_column_range():
    inputs = declared_integer_inputs()

    unbounded = sorted(
        key for key, schema in inputs.items()
        if schema.get("minimum") != DB_INT_MIN or schema.get("maximum") != DB_INT_MAX
    )
    assert unbounded == []


# (METHOD, path template, where, name) -> how to send a value through it.
# `path` and `body` take the value under test; the other ids are real rows,
# so the value under test is the only thing that can be out of range.
CASES = {
    ("POST", "/users/", "body", "group_id"): (
        lambda v, r: "/users/",
        lambda v, r: {"personal_number": "p999", "full_name": "x",
                      "password": "Aa1!aaaaaaaa", "group_id": v}),
    ("PUT", "/users/{user_id}/group", "path", "user_id"): (
        lambda v, r: f"/users/{v}/group", lambda v, r: {"group_id": r["group"]}),
    ("PUT", "/users/{user_id}/group", "body", "group_id"): (
        lambda v, r: f"/users/{r['user']}/group", lambda v, r: {"group_id": v}),
    ("POST", "/equipment/", "body", "group_id"): (
        lambda v, r: "/equipment/", lambda v, r: {"catalog_name": "M4", "group_id": v}),
    ("PATCH", "/equipment/{equipment_id}/sensitivity", "path", "equipment_id"): (
        lambda v, r: f"/equipment/{v}/sensitivity", lambda v, r: {"sensitivity": "UNCLASSIFIED"}),
    ("POST", "/equipment/assign_owner/", "body", "equipment_id"): (
        lambda v, r: "/equipment/assign_owner/", lambda v, r: {"equipment_id": v, "owner_id": r["user"]}),
    ("POST", "/equipment/assign_owner/", "body", "owner_id"): (
        lambda v, r: "/equipment/assign_owner/", lambda v, r: {"equipment_id": r["item"], "owner_id": v}),
    ("POST", "/equipment/transfer", "body", "equipment_id"): (
        lambda v, r: "/equipment/transfer", lambda v, r: {"equipment_id": v, "to_holder_id": r["user"]}),
    ("POST", "/equipment/transfer", "body", "to_holder_id"): (
        lambda v, r: "/equipment/transfer", lambda v, r: {"equipment_id": r["item"], "to_holder_id": v}),
    ("POST", "/equipment/{equipment_id}/verify", "path", "equipment_id"): (
        lambda v, r: f"/equipment/{v}/verify", None),
    ("POST", "/maintenance/report", "body", "equipment_id"): (
        lambda v, r: "/maintenance/report",
        lambda v, r: {"equipment_id": v, "fault_name": "X", "description": "d"}),
    ("POST", "/maintenance/fix/{equipment_id}", "path", "equipment_id"): (
        lambda v, r: f"/maintenance/fix/{v}", None),
    ("PATCH", "/groups/{group_id}", "path", "group_id"): (
        lambda v, r: f"/groups/{v}", lambda v, r: {"timezone": "Europe/Vienna"}),
    ("PUT", "/setup/fault_types/{fault_id}/approve", "path", "fault_id"): (
        lambda v, r: f"/setup/fault_types/{v}/approve", None),
    ("DELETE", "/setup/fault_types/{fault_id}", "path", "fault_id"): (
        lambda v, r: f"/setup/fault_types/{v}", None),
    ("POST", "/verifications/", "body", "equipment_id"): (
        lambda v, r: "/verifications/",
        lambda v, r: {"equipment_id": v, "verification_type": "Daily", "reported_status": "Functional"}),
    ("GET", "/verifications/equipment/{equipment_id}", "path", "equipment_id"): (
        lambda v, r: f"/verifications/equipment/{v}", None),
    ("GET", "/equipment/{equipment_id}/history", "path", "equipment_id"): (
        lambda v, r: f"/equipment/{v}/history", None),
}


def test_the_cases_below_are_every_integer_input_the_application_declares():
    """So a new integer input needs a case here as well as the bound."""
    assert set(CASES) == set(declared_integer_inputs())


@pytest.fixture
def rows(db_session, mock_matrix_db):
    return {
        "item": db_session.query(models.Equipment).order_by(models.Equipment.id).first().id,
        "user": db_session.query(models.User).filter_by(personal_number="u_soldier_a").one().id,
        "group": db_session.query(authz.Group).order_by(authz.Group.id).first().id,
    }


def _send(client, key, value, rows):
    method, _, _, _ = key
    path, body = CASES[key]
    kwargs = {"headers": create_auth_header("u_master")}
    if body is not None:
        kwargs["json"] = body(value, rows)
    return client.request(method, path(value, rows), **kwargs)


@pytest.mark.parametrize("value", TOO_BIG + TOO_SMALL)
@pytest.mark.parametrize("key", sorted(CASES))
def test_an_integer_outside_the_column_range_is_refused_with_422(client, rows, key, value):
    """The suite's client re-raises a server error, so before the fix the
    2**63 and 10**20 cases did not even reach an assertion."""
    _, _, where, name = key

    res = _send(client, key, value, rows)

    assert res.status_code == 422, (key, value, res.text)
    locations = [error["loc"] for error in res.json()["detail"]]
    assert [where, name] in locations or [where, *name.split(".")] in locations, locations


@pytest.mark.parametrize("value", [DB_INT_MAX, DB_INT_MIN])
@pytest.mark.parametrize("key", sorted(CASES))
def test_the_ends_of_the_range_are_accepted_and_find_nothing(client, rows, key, value):
    """The bound is the column's range, not narrower: its two ends pass the
    check and are looked up like any other id that matches no row."""
    res = _send(client, key, value, rows)

    assert res.status_code != 422, (key, value, res.text)
    assert res.status_code in (403, 404), (key, value, res.text)
