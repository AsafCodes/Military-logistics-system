"""
Edge-case coverage added beyond the restored INF-C3 suite: authentication
rejection paths, the transfer endpoint's validation ordering, and a
sibling-battalion prefix collision (SEC-H2) that the restored suite's fixture
data never happened to exercise (all pre-existing battalions in
tests/conftest.py share exact-length paths, so a string-prefix collision was
never triggered by any restored test).
"""
from datetime import datetime

import pytest

from backend import models, security
from tests.conftest import create_auth_header


def test_unauthenticated_request_is_rejected(client):
    """No Authorization header at all must be rejected outright, not
    silently treated as an anonymous/empty-scope user."""
    res = client.get("/equipment/accessible")
    assert res.status_code == 401


def test_invalid_token_is_rejected(client):
    """A garbage bearer token must be rejected by JWT decoding, not treated
    as a valid session for some default user."""
    res = client.get(
        "/equipment/accessible",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert res.status_code == 401


def test_transfer_requires_a_target(client, mock_matrix_db, token_bat_cmdr):
    """XOR validation (equipment.py:134) runs before the equipment lookup:
    neither to_holder_id nor to_location -> 400, even for a nonexistent id."""
    res = client.post(
        "/equipment/transfer",
        json={"equipment_id": 999999},
        headers=token_bat_cmdr,
    )
    assert res.status_code == 400


def test_transfer_rejects_both_targets(client, mock_matrix_db, token_bat_cmdr):
    """XOR validation (equipment.py:136): both to_holder_id and to_location
    supplied together -> 400."""
    res = client.post(
        "/equipment/transfer",
        json={"equipment_id": 999999, "to_holder_id": 1, "to_location": "Armory"},
        headers=token_bat_cmdr,
    )
    assert res.status_code == 400


def test_transfer_nonexistent_equipment_returns_404(client, mock_matrix_db, token_bat_cmdr):
    """A well-formed transfer against an equipment id that doesn't exist ->
    404, not an unguarded None-attribute crash."""
    res = client.post(
        "/equipment/transfer",
        json={"equipment_id": 999999, "to_location": "Armory"},
        headers=token_bat_cmdr,
    )
    assert res.status_code == 404


@pytest.mark.xfail(
    reason=(
        "DATA-H6: equipment.py:147 raises HTTPException(404) INSIDE the "
        "try block (equipment.py:143-181), so the broad "
        "'except Exception' at equipment.py:183 catches it and re-wraps it "
        "as a 500, embedding the original detail string in the new message "
        "(internals-leak, same pattern as SEC-M8). transfer_equipment DOES "
        "validate its target before writing the foreign key -- unlike the "
        "sibling assign_owner route (DATA-M2) -- it just reports the "
        "failure at the wrong status code. Delete this marker when DATA-H6 "
        "moves the 404 outside the try block."
    ),
    strict=False,
)
def test_transfer_nonexistent_target_user_returns_404(client, db_session, mock_matrix_db, token_bat_cmdr):
    """Transferring to a to_holder_id that doesn't exist should -> 404."""
    item = db_session.query(models.Equipment).filter(models.Equipment.serial_number == "TA300").first()
    res = client.post(
        "/equipment/transfer",
        json={"equipment_id": item.id, "to_holder_id": 999999},
        headers=token_bat_cmdr,
    )
    assert res.status_code == 404


@pytest.mark.xfail(
    reason=(
        "SEC-H2: dependencies.scope_equipment_query() builds the battalion "
        "prefix by joining the first two materialised-path segments and "
        "matching with plain str.startswith(), with no trailing-separator "
        "anchor. Battalion '188/5' is therefore a string-prefix of sibling "
        "battalion '188/53' -- '188/53/A'.startswith('188/5') is True in "
        "Python (verified empirically before writing this test) -- so a "
        "battalion-5 commander also sees battalion 53's equipment. Delete "
        "this marker when SEC-H2 anchors the prefix match."
    ),
    strict=False,
)
def test_sibling_battalion_prefix_collision(client, db_session, mock_matrix_db):
    battalion_cmdr_profile = (
        db_session.query(models.Profile)
        .filter(models.Profile.name == "Battalion Tech Commander")
        .first()
    )
    intruder = models.User(
        personal_number="u_bat5_cmdr",
        full_name="Cmdr Battalion 5",
        battalion="5",
        company="HQ",
        password_hash=security.get_password_hash("secret"),
        role=models.UserRole.TECHNICIAN_MANAGER,
        profile_id=battalion_cmdr_profile.id,
        unit_path="Brigade1/Bat5",
        unit_hierarchy="188/5",
    )
    db_session.add(intruder)
    db_session.commit()

    sibling_item = models.Equipment(
        catalog_item_id=db_session.query(models.CatalogItem).first().id,
        status="Functional",
        unit_hierarchy="188/53/A",  # belongs to sibling battalion 53, not battalion 5
        serial_number="SEC_H2_PROBE",
        last_verified_at=datetime.utcnow(),
    )
    db_session.add(sibling_item)
    db_session.commit()

    headers = create_auth_header("u_bat5_cmdr")
    res = client.get("/equipment/accessible", headers=headers)
    assert res.status_code == 200
    sns = [i["serial_number"] for i in res.json()]
    assert "SEC_H2_PROBE" not in sns
