"""DATA-M7 and DATA-M11: one compliance vocabulary, computed in one place.

DATA-M7. The inventory report renamed GOOD to "Reported" and passed WARNING
and SEVERE through; the schema meant to describe it promised "Reported",
"Late", "Missing"; the report page tested for "Reported" and printed every
other value as it came, in English, in a Hebrew UI. Three vocabularies for one
fact.

DATA-M11. dependencies.get_daily_status repeated the rules of
models.Equipment.compliance_level, and the routes were split between the two.

Now enums.ComplianceLevel is the vocabulary, the property is the only
implementation, and the report sends what /equipment/accessible sends. These
tests pin each link of that chain:

- the frontend's ComplianceLevel alias holds exactly the enum's values;
- the report produces all three values, each equal to what the equipment
  list and the caller's own equipment say about the same item;
- OpenAPI describes both fields as the enum, so the contract is published;
- no backend module outside enums.py spells a value as a bare string, which
  is how a second implementation or a renamed value would come back;
- get_daily_status stays deleted.

The report page's labels for the three values are pinned in
GeneralReportPage.test.tsx.
"""
import ast
from datetime import timedelta
from pathlib import Path

import pytest

from backend import clock, dependencies, models
from backend.enums import ComplianceLevel
from backend.main import app
from tests.test_report_item_contract import TYPES_FILE, literal_alias

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
VALUES = {member.value for member in ComplianceLevel}


def test_the_frontend_alias_holds_exactly_the_enum_values():
    assert literal_alias(TYPES_FILE.read_text(encoding="utf-8"), "ComplianceLevel") == VALUES


@pytest.fixture
def three_ages(db_session, mock_matrix_db):
    """One item per value: verified an hour ago, thirty hours ago, never.

    All three held by u_master, the caller below, so they also appear on
    /users/me/equipment. Measured in DATA-M7's mutant battery: before this
    test read that route, making it send a constant GOOD passed the whole
    suite.
    """
    template = db_session.query(models.Equipment).filter_by(serial_number="SA100").one()
    master = db_session.query(models.User).filter_by(personal_number="u_master").one()
    now = clock.utcnow()
    ages = {
        "M7-RECENT": (now - timedelta(hours=1), ComplianceLevel.GOOD),
        "M7-STALE": (now - timedelta(hours=30), ComplianceLevel.WARNING),
        "M7-NEVER": (None, ComplianceLevel.SEVERE),
    }
    for serial, (verified_at, _) in ages.items():
        db_session.add(models.Equipment(
            catalog_item_id=template.catalog_item_id,
            group_id=template.group_id,
            serial_number=serial,
            holder_user_id=master.id,
            last_verified_at=verified_at,
        ))
    db_session.commit()
    return {serial: level.value for serial, (_, level) in ages.items()}


def _by_serial(rows):
    return {row["serial_number"]: row for row in rows}


def test_the_report_sends_each_level_and_agrees_with_both_equipment_lists(
    client, token_master, three_ages
):
    responses = {
        url: client.get(url, headers=token_master)
        for url in ("/reports/query", "/equipment/accessible", "/users/me/equipment")
    }
    for url, response in responses.items():
        assert response.status_code == 200, (url, response.text)
    reported = _by_serial(responses["/reports/query"].json())
    listed = _by_serial(responses["/equipment/accessible"].json())
    mine = _by_serial(responses["/users/me/equipment"].json())

    for serial, expected in three_ages.items():
        assert reported[serial]["reporting_status"] == expected, serial
        assert listed[serial]["compliance_level"] == expected, serial
        assert mine[serial]["compliance_level"] == expected, serial

    # Every row, not just the three made here: the two routes agree on all of
    # them, and no row carries a value outside the enum.
    assert {row["reporting_status"] for row in reported.values()} <= VALUES
    for serial, row in reported.items():
        assert row["reporting_status"] == listed[serial]["compliance_level"], serial


def test_openapi_publishes_both_fields_as_the_enum():
    spec = app.openapi()
    schemas = spec["components"]["schemas"]
    assert set(schemas["ComplianceLevel"]["enum"]) == VALUES

    ref = {"$ref": "#/components/schemas/ComplianceLevel"}
    assert schemas["InventoryReportItem"]["properties"]["reporting_status"] == ref
    assert schemas["EquipmentResponse"]["properties"]["compliance_level"] == ref

    report = spec["paths"]["/reports/query"]["get"]["responses"]["200"]["content"]["application/json"]
    assert report["schema"]["items"] == {"$ref": "#/components/schemas/InventoryReportItem"}


def _docstrings(tree):
    """The string constants that are docstrings, which may name the values."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


def test_no_backend_module_spells_a_level_as_a_bare_string():
    offenders = []
    for path in sorted(BACKEND.rglob("*.py")):
        if path.name == "enums.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstrings = _docstrings(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and node.value in VALUES
                and id(node) not in docstrings
            ):
                offenders.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno} {node.value!r}")

    assert offenders == [], offenders


def test_the_second_implementation_stays_deleted():
    assert not hasattr(dependencies, "get_daily_status")
