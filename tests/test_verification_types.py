"""API-M5: a condition report may not call itself a presence check.

Two routes stamp the compliance clock, and they are two acts on purpose
(SYSTEM_MAP.md, the possession note). POST /equipment/{id}/verify is the
holder's own "I have this item", gated on possession alone. POST /verifications/
is a condition report, gated on possession OR REPORT_STATUS. The form behind
the second offered "presence_check" as its type, by default, so a commander
could confirm presence of kit they do not hold through the wider gate, and the
clock took it.

verification_type is now enums.VerificationType, one member, on input only.
These tests hold the list closed, hold a refused type to writing nothing, and
hold the frontend to the same list.
"""
import re
from pathlib import Path

import pytest

from backend import models
from backend.enums import EventType, VerificationType
from tests.conftest import create_auth_header
from tests.test_audit_trail import item_named, logs_for
from tests.test_verification_clock import STALE, fresh_clock, set_clock

FRONTEND_SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"

# A presence check under each spelling a caller has used or might, the dropped
# transfer type, the values this suite's fixtures sent before API-M5, and the
# allowed value in the wrong case: the enum matches exactly, not loosely.
REFUSED = [
    "presence_check",
    "PRESENCE_CHECK",
    "transfer_verification",
    "daily",
    "Daily",
    "DAILY",
    "Routine",
    "CONDITION_REPORT",
    # Sent but empty: a default fills only a field that is left out.
    "",
    None,
    1,
]


def report(item, verification_type, reported_status="Malfunctioning"):
    return {
        "equipment_id": item.id,
        "verification_type": verification_type,
        "reported_status": reported_status,
        "findings": "scratched housing",
        "action_required": False,
    }


def written(db_session, item):
    """Everything a condition report writes, counted for one item."""
    db_session.expire_all()
    return {
        "verifications": db_session.query(models.Verification).filter_by(equipment_id=item.id).count(),
        "condition_reports": len(logs_for(db_session, item, EventType.CONDITION_REPORT)),
        "status_history": db_session.query(models.EquipmentStatusHistory)
        .filter_by(equipment_id=item.id)
        .count(),
    }


def test_the_list_is_a_condition_report_and_nothing_else():
    assert {member.value for member in VerificationType} == {"condition_report"}


@pytest.mark.parametrize("verification_type", REFUSED)
def test_a_type_outside_the_list_is_refused_and_writes_nothing(
    client, db_session, mock_matrix_db, verification_type
):
    """u_cmdr_a holds REPORT_STATUS over SA100 and does not hold the item:
    exactly the caller a presence check through this route let in.

    The refusal is paired with the same caller filing the same report as a
    condition report, which writes a row in each table. Without that control,
    "nothing written" would hold for a request that never reached the route
    for any reason at all.
    """
    item = set_clock(db_session, "SA100", STALE)
    before = item.last_verified_at
    headers = create_auth_header("u_cmdr_a")

    res = client.post("/verifications/", json=report(item, verification_type), headers=headers)

    assert res.status_code == 422, res.text
    locations = [error["loc"] for error in res.json()["detail"]]
    assert ["body", "verification_type"] in locations, locations
    assert written(db_session, item) == {
        "verifications": 0, "condition_reports": 0, "status_history": 0,
    }
    assert fresh_clock(db_session, "SA100") == before

    res = client.post("/verifications/", json=report(item, "condition_report"), headers=headers)

    assert res.status_code == 200, res.text
    assert written(db_session, item) == {
        "verifications": 1, "condition_reports": 1, "status_history": 1,
    }
    assert fresh_clock(db_session, "SA100") != before


def test_the_type_may_be_left_out_and_is_stored_as_a_condition_report(
    client, db_session, mock_matrix_db
):
    item = item_named(db_session, "SA100")
    body = report(item, None, reported_status="Functional")
    del body["verification_type"]

    res = client.post("/verifications/", json=body, headers=create_auth_header("u_cmdr_a"))

    assert res.status_code == 200, res.text
    assert res.json()["verification_type"] == "condition_report"
    db_session.expire_all()
    stored = db_session.query(models.Verification).filter_by(equipment_id=item.id).one()
    # The exact string. (The route stores `.value`, as it does reported_status;
    # the str-mixin member would bind to the same string on SQLite, measured,
    # so this does not tell the two apart.)
    assert stored.verification_type == "condition_report"


def test_a_row_written_before_the_list_still_reads_back(client, db_session, mock_matrix_db):
    """The column has no CHECK, and rows from before API-M5 hold what the old
    form sent. The response field stays a str so the history still opens."""
    item = item_named(db_session, "SA100")
    db_session.add(models.Verification(
        equipment_id=item.id,
        verification_type="presence_check",
        reported_status="Functional",
        created_by=mock_matrix_db["company_cmdr_a"].id,
    ))
    db_session.commit()

    res = client.get(f"/verifications/equipment/{item.id}", headers=create_auth_header("u_cmdr_a"))

    assert res.status_code == 200, res.text
    assert [row["verification_type"] for row in res.json()] == ["presence_check"]


def _frontend_sources():
    return [
        path for path in FRONTEND_SRC.rglob("*")
        if path.suffix in (".ts", ".tsx") and not re.search(r"\.(test|spec)\.tsx?$", path.name)
    ]


def test_the_frontend_sends_only_types_on_the_list():
    """Every verification_type literal outside the tests is a member, and the
    two dropped values are gone from the source altogether."""
    values = {member.value for member in VerificationType}
    sent = []
    for path in _frontend_sources():
        source = path.read_text(encoding="utf-8")
        for dropped in ("presence_check", "transfer_verification"):
            assert dropped not in source, f"{path.name} still names {dropped!r}"
        sent += [
            (path.name, literal)
            for literal in re.findall(r"""verification_type\s*:\s*['"]([^'"]*)['"]""", source)
        ]

    assert sent, "no verification_type literal found, so this test checks nothing"
    assert all(literal in values for _, literal in sent), sent
