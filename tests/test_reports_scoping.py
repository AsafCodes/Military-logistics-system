"""Visibility on /reports/query, which until H1-5 had no coverage at all.

That absence is the whole story of DATA-H9. The route carried its own copy of
the scoping ladder, the copy drifted from the original (it dropped the
unit_path fallback), and nothing failed -- because nothing ever asked this
endpoint what it returned. H1-5 deletes the copy and routes the endpoint
through dependencies.scope_equipment_query, so what needs pinning now is not
the ladder's behaviour but the sharing itself.

test_the_report_and_the_listing_cannot_diverge is the load-bearing one. The
per-account cases below would all still pass against a second implementation
that happened to agree today; only an item-for-item comparison against the
other endpoint fails the moment someone reintroduces one.
"""
import pytest
from sqlalchemy import event

from backend import authz, models

# The fixture inventory, by the company holding it (tests/conftest.py).
CO_A_ITEMS = {"SA100", "TA300"}
CO_B_ITEMS = {"SB200"}
ALL_ITEMS = CO_A_ITEMS | CO_B_ITEMS


def serials(response):
    assert response.status_code == 200
    return {row["serial_number"] for row in response.json()}


def ids(response):
    assert response.status_code == 200
    return {row["id"] for row in response.json()}


# --- unit_association, after H1-11 dropped the column it used to read ------


def association(response, serial):
    assert response.status_code == 200
    return next(row["unit_association"] for row in response.json() if row["serial_number"] == serial)


def test_the_unit_column_reads_the_group_the_item_belongs_to(
    client, mock_matrix_db, token_master
):
    """Same strings as before H1-11, from a different place.

    unit_association served Equipment.unit_hierarchy until H1-11 dropped it.
    The values are unchanged because the seed's path strings WERE the group
    names -- which is what made the migration's backfill possible -- so this
    would pass against either implementation on its own. The next test is the
    one that distinguishes them.
    """
    report = client.get("/reports/query", headers=token_master)
    assert association(report, "SA100") == "188/53/A"
    assert association(report, "SB200") == "188/53/B"


def test_the_unit_column_follows_the_group_rather_than_a_copy_of_its_name(
    client, db_session, mock_matrix_db, group_graph, token_master
):
    """Rename the group; the report follows within the same request cycle.

    This is the assertion the old implementation could not pass. A column
    holding a copy of the name goes stale the moment the group is renamed,
    and the report then shows a unit that no longer exists -- the two
    representations drifting apart, which is the defect the whole phase
    exists to end, showing up in a place nobody would look for it.
    """
    group_graph["188/53/A"].name = "188/53/Alpha"
    db_session.commit()

    report = client.get("/reports/query", headers=token_master)
    assert association(report, "SA100") == "188/53/Alpha"
    assert association(report, "SB200") == "188/53/B"


def test_the_unit_column_is_loaded_eagerly_and_not_once_per_row(
    client, db_session, mock_matrix_db, token_master
):
    """The joinedload is part of the fix, not a decoration.

    item.group.name on a lazily loaded relationship is one SELECT per distinct
    group across the whole force. Counting statements is the only way to
    notice, because dropping the joinedload changes no response body: every
    other test in this file stays green while the report degrades quietly with
    the size of the inventory.

    expunge_all() first, and it is load-bearing. The fixture builds the Group
    rows in this same session, so without it a lazy load is answered from the
    identity map and emits no SQL at all -- the first version of this test
    passed with the joinedload deleted for exactly that reason.
    """
    standalone_group_loads = []

    def record(conn, cursor, statement, *args):
        # A lazy load is its own SELECT against groups. The eager version
        # mentions groups too, but inside the SELECT against equipment.
        if statement.lstrip().startswith("SELECT groups."):
            standalone_group_loads.append(statement)

    bind = db_session.get_bind()
    db_session.expunge_all()
    event.listen(bind, "before_cursor_execute", record)
    try:
        assert client.get("/reports/query", headers=token_master).status_code == 200
    finally:
        event.remove(bind, "before_cursor_execute", record)

    assert standalone_group_loads == [], (
        f"{len(standalone_group_loads)} lazy group loads -- the joinedload is gone"
    )


def test_a_company_commander_sees_their_own_company_and_not_a_sibling(
    client, mock_matrix_db, token_company_cmdr
):
    """Co A's commander holds VIEW on Co A. Co B is a sibling, not a descendant."""
    assert serials(client.get("/reports/query", headers=token_company_cmdr)) == CO_A_ITEMS


def test_a_battalion_commander_sees_both_companies_beneath_them(
    client, mock_matrix_db, token_bat_cmdr
):
    """Authority is positional: the same VIEW grant, one level up, spans both."""
    assert serials(client.get("/reports/query", headers=token_bat_cmdr)) == ALL_ITEMS


def test_a_soldier_sees_only_the_item_they_hold(client, mock_matrix_db, token_soldier):
    """Membership is not authority. Soldier A is IN Co A and commands none of it,
    so the holder arm is the only thing putting anything on their report."""
    assert serials(client.get("/reports/query", headers=token_soldier)) == {"SA100"}


def test_a_user_with_no_grant_sees_only_the_item_they_hold(
    client, mock_matrix_db, token_soldier
):
    """Possession is a reason to see one item, never a reason to see a unit.

    Written against the SOLDIER, and the account changed in H1-10 rather than
    the claim. It used to be the company tech, on the strength of this
    fixture's Company Tech Soldier row setting can_view_company_realtime
    false -- a value profiles.py never had. H1-10 reconciled the two the
    other way and granted the techs sight of the unit they maintain, so the
    tech is no longer an example of a grantless user and this test would
    have quietly become a test of something else.
    """
    assert serials(client.get("/reports/query", headers=token_soldier)) == {"SA100"}


def test_a_user_holding_nothing_and_granted_nothing_sees_an_empty_report(
    client, db_session, mock_matrix_db, token_brigade_tech
):
    """Empty, not everything. A missing grant must never read as unscoped --
    that inversion is the failure mode worth a test of its own.

    The premise is now established INSIDE the test rather than inherited from
    the fixture's silence, because that silence is exactly what H1-10 changed:
    brigade_tech gained a root VIEW grant, and after it no seeded account
    both holds nothing and is granted nothing. Stripping the grants here says
    out loud which condition the assertion depends on, so the next entry to
    move a grant table cannot silently empty this test of meaning.
    """
    db_session.query(authz.Grant).filter_by(
        user_id=mock_matrix_db["brigade_tech"].id
    ).delete(synchronize_session=False)
    db_session.commit()

    assert serials(client.get("/reports/query", headers=token_brigade_tech)) == set()


def test_master_sees_the_whole_force(client, mock_matrix_db, token_master):
    assert serials(client.get("/reports/query", headers=token_master)) == ALL_ITEMS


@pytest.mark.parametrize(
    "token_fixture",
    [
        "token_master",
        "token_brigade_cmdr",
        "token_brigade_tech",
        "token_bat_cmdr",
        "token_bat_tech",
        "token_company_cmdr",
        "token_company_tech",
        "token_soldier",
    ],
)
def test_the_report_and_the_listing_cannot_diverge(
    client, mock_matrix_db, request, token_fixture
):
    """The two endpoints must select the same rows for the same user, always.

    This is the assertion DATA-H9 needed and never had. Comparing ids rather
    than serial numbers on purpose: serial_number is nullable, and two NULLs
    would collapse into one set member and hide a difference.
    """
    headers = request.getfixturevalue(token_fixture)
    assert ids(client.get("/reports/query", headers=headers)) == ids(
        client.get("/equipment/accessible", headers=headers)
    )


def test_a_user_filter_narrows_the_scope_and_cannot_widen_it(
    client, db_session, mock_matrix_db, token_company_cmdr
):
    """Scoping is applied before the query parameters, so a filter can only
    ever remove rows. Searching Co A's commander for the holder of a Co B item
    must return nothing rather than reaching across the boundary to find them.
    """
    soldier_b = (
        db_session.query(models.User).filter_by(personal_number="u_soldier_b").one()
    )
    res = client.get(
        "/reports/query",
        params={"holder_name": soldier_b.full_name},
        headers=token_company_cmdr,
    )
    assert serials(res) == set()

    # The same filter for someone inside the scope still works, so the empty
    # result above is the boundary and not a broken join.
    soldier_a = (
        db_session.query(models.User).filter_by(personal_number="u_soldier_a").one()
    )
    res = client.get(
        "/reports/query",
        params={"holder_name": soldier_a.full_name},
        headers=token_company_cmdr,
    )
    assert serials(res) == {"SA100"}
