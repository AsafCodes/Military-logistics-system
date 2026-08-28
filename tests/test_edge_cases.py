"""
Edge-case coverage added beyond the restored INF-C3 suite: authentication
rejection paths and the transfer endpoint's validation ordering.

This module also carried a sibling-battalion prefix collision (SEC-H2), where
battalion "188/5" was a string-prefix of sibling "188/53" and its commander saw
the wrong battalion's equipment. H1-5 deleted it along with the prefix match
itself: scoping is now a join against the group closure, "188/5" is simply a
group with no edge to "188/53", and there is no string comparison left to
collide. The scenario is not fixed here, it is no longer expressible.
"""

from backend import models


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


def test_transfer_nonexistent_target_user_returns_404(client, db_session, mock_matrix_db, token_bat_cmdr):
    """Transferring to a to_holder_id that does not exist -> 404, not 500.

    DATA-H6 lived here and was xfail-marked from the audit until H1-10.5. The
    target lookup sat INSIDE transfer_equipment's try block, whose broad
    `except Exception` re-wrapped the deliberate 404 as a 500 and embedded the
    original detail string in the new message -- the wrong status code and an
    internals leak, from one misplaced statement.

    The lookup is a pure read and now sits above the try with the other
    resolution steps. An `except HTTPException: raise` clause was added ahead
    of the broad one as well, so the next deliberate status code raised in
    there survives too.
    """
    item = db_session.query(models.Equipment).filter(models.Equipment.serial_number == "TA300").first()
    res = client.post(
        "/equipment/transfer",
        json={"equipment_id": item.id, "to_holder_id": 999999},
        headers=token_bat_cmdr,
    )
    assert res.status_code == 404
