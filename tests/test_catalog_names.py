"""DATA-M6: one catalog row per item type, and only a catalog manager adds one.

create_equipment takes the item type as free text. Until DATA-M6 it looked the
name up exactly and inserted it when absent, so "Radio 710", "radio 710" and
"Radio  710 " became three catalog rows and split every inventory report
three ways, and any CREATE_EQUIPMENT holder could add names. Two concurrent
creates of one new name answered 500 on the unique constraint.

What holds now, and what this file pins:

  - EquipmentCreate normalizes the name: whitespace runs collapse to one
    space, leading and trailing ones go, and a blank name or one over
    CATALOG_NAME_MAX characters is a 422 that writes nothing;
  - the route matches the catalog without case, oldest row first;
  - a name with no match needs MANAGE_CATALOG as well, else 403 and nothing
    written; a new row keeps the caller's casing;
  - losing the race for a new name links to the row that won; an
    IntegrityError that is not that race is not swallowed.

Not pinned, because the app cannot close it: two case variants of one new name
committed at the same moment both land, since the unique constraint is
case-sensitive. On SQLite, lower() folds ASCII only.
"""
import pytest
from sqlalchemy.exc import IntegrityError

from backend import authz, models
from backend.enums import Capability
from backend.routers import equipment as equipment_router
from backend.schemas import CATALOG_NAME_MAX
from tests.conftest import create_auth_header

# u_tech_a holds CREATE_EQUIPMENT over Company A and no MANAGE_CATALOG;
# u_master holds both (tests/conftest.py, mock_matrix_db).
CREATOR = "u_tech_a"
MANAGER = "u_master"


def create(client, who, name, serial):
    return client.post(
        "/equipment/",
        json={"catalog_name": name, "serial_number": serial},
        headers=create_auth_header(who),
    )


def catalog_rows(db_session):
    db_session.expire_all()
    return db_session.query(models.CatalogItem).order_by(models.CatalogItem.id).all()


def catalog_id_of(db_session, serial):
    db_session.expire_all()
    item = db_session.query(models.Equipment).filter_by(serial_number=serial).one()
    return item.catalog_item_id


def radio_id(db_session):
    return db_session.query(models.CatalogItem).filter_by(name="Standard Radio").one().id


def test_a_creator_naming_a_known_item_in_other_case_and_spacing_links_to_it(
    client, db_session, mock_matrix_db
):
    before = len(catalog_rows(db_session))

    res = create(client, CREATOR, "  standard   RADIO ", "M6-KNOWN")

    assert res.status_code == 200, res.text
    assert catalog_id_of(db_session, "M6-KNOWN") == radio_id(db_session)
    assert len(catalog_rows(db_session)) == before


def test_a_creator_without_manage_catalog_cannot_add_a_name(client, db_session, mock_matrix_db):
    catalog_before = len(catalog_rows(db_session))
    equipment_before = db_session.query(models.Equipment).count()

    res = create(client, CREATOR, "Night Scope", "M6-REFUSED")

    assert res.status_code == 403, res.text
    detail = res.json()["detail"]
    assert "MANAGE_CATALOG" in detail and "Night Scope" in detail, detail
    assert len(catalog_rows(db_session)) == catalog_before
    db_session.expire_all()
    assert db_session.query(models.Equipment).count() == equipment_before


def test_every_other_verb_over_the_root_still_cannot_add_a_name(
    client, db_session, mock_matrix_db, group_graph
):
    """The gate asks MANAGE_CATALOG, not any verb that happens to be global.

    In the fixture every account holding CREATE_EQUIPMENT over the root also
    holds MANAGE_CATALOG, so without this the gate could ask the wrong verb
    and every other test here would still pass.
    """
    tech = db_session.query(models.User).filter_by(personal_number=CREATOR).one()
    db_session.add_all(
        authz.Grant(user_id=tech.id, group_id=group_graph["188"].id, capability=cap.value)
        for cap in Capability
        if cap is not Capability.MANAGE_CATALOG
    )
    db_session.commit()
    before = len(catalog_rows(db_session))

    res = create(client, CREATOR, "Night Scope", "M6-ALL-BUT")

    assert res.status_code == 403, res.text
    assert "MANAGE_CATALOG" in res.json()["detail"], res.text
    assert len(catalog_rows(db_session)) == before


def test_a_manager_adds_a_name_once_normalized_and_others_then_reuse_it(
    client, db_session, mock_matrix_db
):
    before = {row.name for row in catalog_rows(db_session)}

    added = create(client, MANAGER, "  Night \t  Scope\n", "M6-NEW-1")
    assert added.status_code == 200, added.text

    rows = catalog_rows(db_session)
    new_names = [row.name for row in rows if row.name not in before]
    assert new_names == ["Night Scope"], new_names
    new_id = catalog_id_of(db_session, "M6-NEW-1")

    reused = create(client, CREATOR, "night scope", "M6-NEW-2")

    assert reused.status_code == 200, reused.text
    assert catalog_id_of(db_session, "M6-NEW-2") == new_id
    assert len(catalog_rows(db_session)) == len(rows)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "\t\n",
        "  ",
        "x" * (CATALOG_NAME_MAX + 1),
        " " + "y" * (CATALOG_NAME_MAX + 1) + " ",
    ],
    ids=["empty", "spaces", "tab-newline", "unicode-spaces", "one-over", "one-over-padded"],
)
def test_a_blank_or_overlong_name_is_a_422_that_writes_nothing(
    client, db_session, mock_matrix_db, name
):
    catalog_before = len(catalog_rows(db_session))
    equipment_before = db_session.query(models.Equipment).count()

    res = create(client, MANAGER, name, "M6-BAD")

    assert res.status_code == 422, res.text
    assert res.json()["detail"][0]["loc"] == ["body", "catalog_name"]
    assert len(catalog_rows(db_session)) == catalog_before
    db_session.expire_all()
    assert db_session.query(models.Equipment).count() == equipment_before


def test_the_cap_counts_the_normalized_name(client, db_session, mock_matrix_db):
    """Exactly CATALOG_NAME_MAX passes, padding included: the cap is on what is stored."""
    name = "z" * CATALOG_NAME_MAX

    res = create(client, MANAGER, "  " + name + "  ", "M6-EDGE")

    assert res.status_code == 200, res.text
    assert name in {row.name for row in catalog_rows(db_session)}


def test_existing_case_duplicates_resolve_to_the_oldest_row(client, db_session, mock_matrix_db):
    """Rows that differ only in case can predate the case-insensitive match."""
    older = models.CatalogItem(name="Radio X")
    db_session.add(older)
    db_session.commit()
    newer = models.CatalogItem(name="radio x")
    db_session.add(newer)
    db_session.commit()
    assert older.id < newer.id

    res = create(client, CREATOR, "RADIO X", "M6-DUP")

    assert res.status_code == 200, res.text
    assert catalog_id_of(db_session, "M6-DUP") == older.id


def test_a_known_name_with_an_accented_capital_matches_itself(client, db_session, mock_matrix_db):
    """Both sides are folded by the database's lower(), never one by Python's.

    SQLite's lower() leaves "É" alone and str.lower() does not, so folding the
    request in Python would make this exact name miss on SQLite and send a
    creator into the MANAGE_CATALOG refusal. On Postgres that depends on the
    locale. The route passes on both, because one function folds both sides.
    """
    known = models.CatalogItem(name="Élan Radio")
    db_session.add(known)
    db_session.commit()

    res = create(client, CREATOR, "Élan Radio", "M6-ACCENT")

    assert res.status_code == 200, res.text
    assert catalog_id_of(db_session, "M6-ACCENT") == known.id


def test_losing_the_race_for_a_new_name_links_to_the_row_that_won(
    client, db_session, mock_matrix_db, monkeypatch
):
    """The lookup misses once, as it would for a request that lost the race.

    The insert then meets the unique constraint. The route must roll back,
    read the row the other request committed, and answer 200, not 500.
    """
    real = equipment_router._find_catalog_item
    calls = []

    def miss_once(db, name):
        calls.append(name)
        return None if len(calls) == 1 else real(db, name)

    monkeypatch.setattr(equipment_router, "_find_catalog_item", miss_once)
    before = len(catalog_rows(db_session))

    res = create(client, MANAGER, "Standard Radio", "M6-RACE")

    assert res.status_code == 200, res.text
    assert len(calls) == 2, calls
    assert catalog_id_of(db_session, "M6-RACE") == radio_id(db_session)
    assert len(catalog_rows(db_session)) == before


def test_an_integrity_error_that_is_not_the_race_is_raised(
    client, db_session, mock_matrix_db, monkeypatch
):
    """If the re-read still finds nothing, the conflict is not explained, so it surfaces."""
    monkeypatch.setattr(equipment_router, "_find_catalog_item", lambda db, name: None)

    with pytest.raises(IntegrityError):
        create(client, MANAGER, "Standard Radio", "M6-UNEXPLAINED")
