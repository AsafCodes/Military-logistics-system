"""SEC-H5: the four listings that returned the whole force to anybody.

Every other read path in the system had been cut onto the group model by H1-10.
These four never had a gate of any kind, and two of them were deferred here *by
name* — `GET /tickets/` by H1-9 and `GET /users` by H1-10 — on the reasoning
that listing scope is one shared fix rather than four bespoke gates.

The shape of the claim is the same for all four and is worth stating once: a
listing must answer **what may you see**, not **are you an administrator**. So
none of these is gated on a capability. Three are scoped through the equipment
they derive from, and the roster is scoped through group membership.

The sharp case throughout is `soldier_a`: no grant of any kind, holding exactly
one item. Anything they can see, they can see for a reason that is not authority.
"""
from backend import authz, models
from tests.conftest import create_auth_header


def get(client, who, path):
    return client.get(path, headers=create_auth_header(who))


# --- analytics --------------------------------------------------------------


def test_readiness_is_counted_over_what_the_caller_can_see(
    client, db_session, mock_matrix_db
):
    """Readiness was the whole force's, for everybody.

    Both counts now run through scope_equipment_query, so the percentage means
    *your* readiness. A private holding one working item is at 100%; a commander
    sees their own unit's true figure.

    Asserted on the item COUNT rather than only the percentage, because a ratio
    hides the leak: 2 of 3 and 200 of 300 are the same number. The count is what
    says how much of the force the caller was shown.
    """
    item = db_session.query(models.Equipment).filter_by(serial_number="SB200").one()
    item.status = "Malfunctioning"
    db_session.commit()

    everything = get(client, "u_master", "/analytics/unit_readiness").json()
    assert everything["total_items"] == 3
    assert everything["functional_items"] == 2

    # Company A's commander: SA100 and TA300, both functional, and no sight of
    # Company B's broken item at all.
    company = get(client, "u_cmdr_a", "/analytics/unit_readiness").json()
    assert company["total_items"] == 2
    assert company["functional_items"] == 2
    assert company["readiness_percentage"] == 100.0

    mine = get(client, "u_soldier_a", "/analytics/unit_readiness").json()
    assert mine["total_items"] == 1


def test_an_empty_scope_reports_no_readiness_rather_than_the_force(
    client, db_session, mock_matrix_db
):
    """Zero, not everything — the inversion that matters.

    A missing scope reading as "unscoped" is the failure mode this whole ticket
    is about, and division guards are exactly where it hides: `total == 0` takes
    a different branch, so an implementation that scoped the numerator and not
    the denominator would look right for every account that can see something.
    """
    db_session.query(models.Equipment).update(
        {models.Equipment.holder_user_id: None}, synchronize_session=False
    )
    db_session.commit()

    res = get(client, "u_soldier_a", "/analytics/unit_readiness").json()
    assert res["total_items"] == 0
    assert res["functional_items"] == 0
    assert res["readiness_percentage"] == 0


# --- the movement report ----------------------------------------------------


def test_the_movement_report_covers_only_visible_equipment(
    client, db_session, mock_matrix_db
):
    """Every handover of every item, force-wide, to any authenticated user.

    A transaction log is exactly as visible as the item it describes, so the
    scope comes from the equipment rather than from the log. Generated through
    the API rather than inserted, so the rows under test are the ones the app
    actually writes.
    """
    a = db_session.query(models.Equipment).filter_by(serial_number="SA100").one()
    b = db_session.query(models.Equipment).filter_by(serial_number="SB200").one()

    for who, item, target in (
        ("u_cmdr_a", a, mock_matrix_db["company_tech_a"]),
        ("u_cmdr_b", b, mock_matrix_db["company_cmdr_b"]),
    ):
        res = client.post(
            "/equipment/transfer",
            json={"equipment_id": item.id, "to_holder_id": target.id},
            headers=create_auth_header(who),
        )
        assert res.status_code == 200, res.text

    def serials(who):
        res = get(client, who, "/reports/daily_movement")
        assert res.status_code == 200, res.text
        return {row["serial_number"] for row in res.json()}

    assert serials("u_master") == {"SA100", "SB200"}
    assert serials("u_cmdr_a") == {"SA100"}
    assert serials("u_cmdr_b") == {"SB200"}


# --- the ticket list --------------------------------------------------------


def test_the_ticket_list_covers_only_visible_equipment(
    client, db_session, mock_matrix_db
):
    """H1-9 deferred this one here by name.

    Scoping that file's two write routes narrowed this by nothing, which the
    note under SEC-H5 says explicitly: the leak is the list, not the ids in it.
    A private could read the whole fleet's fault history and, through
    status_filter, its current unserviceable list.
    """
    a = db_session.query(models.Equipment).filter_by(serial_number="SA100").one()
    b = db_session.query(models.Equipment).filter_by(serial_number="SB200").one()

    for who, item in (("u_cmdr_a", a), ("u_cmdr_b", b)):
        res = client.post(
            "/maintenance/report",
            json={"equipment_id": item.id, "fault_name": "Cracked", "description": "x"},
            headers=create_auth_header(who),
        )
        assert res.status_code == 200, res.text

    def ids(who, path="/tickets/"):
        res = get(client, who, path)
        assert res.status_code == 200, res.text
        return {row["equipment_id"] for row in res.json()}

    assert ids("u_master") == {a.id, b.id}
    assert ids("u_cmdr_a") == {a.id}
    assert ids("u_soldier_a") == {a.id}, "they hold SA100, so its ticket is theirs to see"
    assert ids("u_tech_b") == {b.id}

    # The filter narrows within the scope rather than escaping it.
    assert ids("u_cmdr_a", "/tickets/?status_filter=Open") == {a.id}


# --- the roster -------------------------------------------------------------


def test_the_roster_shows_only_people_in_units_the_caller_can_see(
    client, db_session, mock_matrix_db
):
    """H1-10 deferred this one here by name, and it was the least defended.

    No gate of any kind — not a role check, not a profile flag — returning every
    account's personal_number, which in this domain is the military ID, together
    with its whole permission matrix.

    Scoped rather than gated on MANAGE_PERSONNEL: a company commander should see
    their company without being able to create users. That is the distinction
    between a listing and an administrative act, and it is why the three write
    routes beside this one took the verb and this one did not.
    """
    def roster(who):
        res = get(client, who, "/users")
        assert res.status_code == 200, res.text
        return {row["personal_number"] for row in res.json()}

    everyone = roster("u_master")
    assert "u_soldier_b" in everyone and "u_brig_tech" in everyone

    company_a = roster("u_cmdr_a")
    assert company_a == {"u_cmdr_a", "u_tech_a", "u_soldier_a"}
    assert "u_soldier_b" not in company_a

    battalion = roster("u_bat_cmdr")
    assert {"u_soldier_a", "u_soldier_b", "u_cmdr_a", "u_cmdr_b"} <= battalion


def test_a_user_with_no_grant_still_finds_themselves(
    client, db_session, mock_matrix_db
):
    """The self-arm, which is the half that is not about authority.

    soldier_a commands nothing, so the membership arm returns nothing for them.
    Without the second arm the roster would be empty for exactly the people who
    most need to resolve their own record — and /users/me is a different route,
    so nothing else would notice.
    """
    res = get(client, "u_soldier_a", "/users")
    assert res.status_code == 200
    assert {row["personal_number"] for row in res.json()} == {"u_soldier_a"}


def test_the_roster_search_cannot_reach_outside_the_scope(
    client, db_session, mock_matrix_db
):
    """`q` searches name and personal_number across whatever it is given.

    Applied before the scope it would be a lookup oracle for the entire force by
    military ID, which is the more dangerous half of this endpoint: enumeration
    by exact identifier rather than by listing. Naming the target exactly must
    still return nothing.
    """
    res = get(client, "u_cmdr_a", "/users?q=u_soldier_b")
    assert res.status_code == 200
    assert res.json() == []

    res = get(client, "u_cmdr_a", "/users?q=Soldier")
    assert {row["personal_number"] for row in res.json()} == {"u_soldier_a"}


def test_an_unplaced_account_is_visible_to_whoever_must_place_it(
    client, db_session, mock_matrix_db
):
    """scope_user_query's third arm, still reachable even though create_user
    no longer produces this state itself.

    H1-12 closed the gap that used to make this the common case: create_user
    now requires a group_id and places the account in the same transaction
    that creates it. But a group can still be deleted out from under a
    member -- GroupMembership cascades on Group's ondelete -- so an unplaced
    account remains a real, if rarer, state. The membership arm and the self
    arm both exclude it, so without this third arm an unplaced account would
    be invisible to EVERYONE, including whoever holds MANAGE_PERSONNEL and
    needs to place them.
    """
    new = models.User(personal_number="NEW-1", full_name="Recruit")
    db_session.add(new)
    db_session.commit()

    assert "NEW-1" in {u["personal_number"] for u in get(client, "u_master", "/users").json()}

    # Not to everyone, though -- it is the personnel verb that reveals them.
    assert "NEW-1" not in {u["personal_number"] for u in get(client, "u_cmdr_a", "/users").json()}

    # And once placed, they scope like anybody else: visible to their own
    # company's commander, and no longer surfaced by the unplaced arm.
    db_session.add(authz.GroupMembership(
        user_id=new.id,
        group_id=db_session.query(authz.Group).filter_by(name="188/53/A").one().id,
    ))
    db_session.commit()

    assert "NEW-1" in {u["personal_number"] for u in get(client, "u_cmdr_a", "/users").json()}
