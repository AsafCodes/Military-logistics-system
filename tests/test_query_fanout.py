"""DATA-H8 -- the hot read paths must not issue one query per row.

Four routes built a response by looping over rows and touching computed
properties, each of which walks a relationship: up to four extra round trips per
item on the most-requested reads in the system.

None of that is visible from outside. The response bytes are byte-identical
whether the route eager-loads or fans out, so every assertion here counts SQL
statements instead of reading JSON.

TWO THINGS MAKE A TEST IN THIS FILE SILENTLY VACUOUS. Both are easy to
reintroduce and neither fails loudly:

1. A WARM IDENTITY MAP. Reading the seeded rows back before measuring serves
   every lazy load from memory, and an unfixed route measures the same as a
   fixed one (2 statements vs 2, where the real answer is 29 vs 2). The
   count_queries fixture expires the session on entry so this cannot happen by
   omission -- see its docstring for the measurements.

2. SHARED RELATED ROWS. The identity map caches by primary key, so N items all
   pointing at ONE CatalogItem lazy-load it once no matter how large N is. A
   fan-out test built on rows that share a catalog entry, a holder or an owner
   measures a constant either way and passes with every joinedload deleted.
   _stock therefore gives every item its own catalog row, its own holder and its
   own owner. That is not tidiness; it is the whole sensitivity of the suite.

The guard against both is test_the_counter_can_actually_see_a_fanout below,
which strips the loads back out and asserts the count DOES explode. If that
test ever passes trivially, nothing else in this file means anything.
"""
import pytest
import sqlalchemy.orm

from backend import models
from backend.enums import ChangeReason
from tests.conftest import create_auth_header

# --- fixture construction ---------------------------------------------------

def _stock(db_session, group, n, *, tag, start=0, holder_id=None, at_location=False):
    """n equipment rows in `group`, each with related rows of its OWN.

    holder_id=None   -> mint a fresh holder per row, distinct from the owner, so
                        current_state_description walks holder AND owner.
    holder_id=<id>   -> every row held by that one person, which is the shape
                        /users/me/equipment returns.
    at_location=True -> no holder and no custom_location, so the property falls
                        to the actual_location_id branch and walks `location`.
                        The only shape that reaches that load through a listing,
                        since no route ever writes a non-null actual_location_id
                        (DATA-M18) -- such rows arrive by migration or import.

    `tag` namespaces every generated name, and `start` offsets them: CatalogItem
    .name, Location.name and User.personal_number are all UNIQUE, so two calls
    sharing both collide on insert.

    THE DISTINCTNESS IS THE POINT, not tidiness. The identity map caches by
    primary key, so rows sharing one catalog entry or one holder lazy-load it
    once however many rows there are -- and a fan-out test built on them
    measures a constant whether or not the route eager-loads.
    """
    for i in range(start, start + n):
        catalog = models.CatalogItem(name=f"{tag}-cat-{i}")
        owner = models.User(
            personal_number=f"{tag}-owner-{i}", full_name=f"Owner {i}",
            password_hash="x",
        )
        db_session.add_all([catalog, owner])

        holder = holder_id
        location = None
        if at_location:
            row_location = models.Location(name=f"{tag}-loc-{i}", type="Armory")
            db_session.add(row_location)
            db_session.flush()
            location = row_location.id
        elif holder is None:
            fresh = models.User(
                personal_number=f"{tag}-holder-{i}", full_name=f"Holder {i}",
                password_hash="x",
            )
            db_session.add(fresh)
            db_session.flush()
            holder = fresh.id

        db_session.flush()
        db_session.add(models.Equipment(
            catalog_item_id=catalog.id, owner_user_id=owner.id,
            holder_user_id=holder, actual_location_id=location,
            status="Functional", group_id=group.id,
            sensitivity="UNCLASSIFIED", serial_number=f"{tag.upper()}{i}",
        ))
    db_session.commit()


def _stock_history(db_session, equipment_id, n, *, start=0):
    """n verification + status-history rows, each filed by a DIFFERENT person.

    Distinct reporters for the same reason _stock uses distinct holders: rows
    sharing one author lazy-load that author once and the count stays flat.
    Realistic too -- an item passing through many hands is the case that hurts.
    """
    for i in range(start, start + n):
        author = models.User(
            personal_number=f"fanout-rep-{i}", full_name=f"Reporter {i}",
            password_hash="x",
        )
        db_session.add(author)
        db_session.flush()

        verification = models.Verification(
            equipment_id=equipment_id, verification_type="DAILY",
            reported_status="Functional", findings=f"finding {i}",
            created_by=author.id,
        )
        db_session.add(verification)
        db_session.flush()

        db_session.add(models.EquipmentStatusHistory(
            equipment_id=equipment_id, old_status="Functional",
            new_status="Functional", change_reason=ChangeReason.VERIFICATION.value,
            verification_id=verification.id, notes=f"note {i}",
            created_by=author.id,
        ))
    db_session.commit()


def _measure(client, count_queries, url, token):
    """(row count, statement count) for one request."""
    with count_queries() as log:
        response = client.get(url, headers=token)
    assert response.status_code == 200, response.text
    return len(response.json()), log


# --- the four sites ---------------------------------------------------------

def test_accessible_listing_does_not_fan_out(
    client, db_session, group_graph, mock_matrix_db, count_queries
):
    """GET /equipment/accessible -- the site that pins all four loads.

    Viewed as company_cmdr_a, who holds VIEW over 188/53/A and NOT
    VIEW_CLASSIFIED, which is why every stocked row is UNCLASSIFIED: a
    classified one would be filtered out and quietly shrink the sample.

    Both row shapes are stocked and both grow, so a deleted joinedload has
    something to fan out on whichever of the four it was.
    """
    token = create_auth_header("u_cmdr_a")
    company_a = group_graph["188/53/A"]

    _stock(db_session, company_a, 2, tag="held", start=0)
    _stock(db_session, company_a, 2, tag="stored", start=50, at_location=True)
    small_rows, small = _measure(client, count_queries, "/equipment/accessible", token)

    _stock(db_session, company_a, 6, tag="held", start=100)
    _stock(db_session, company_a, 6, tag="stored", start=150, at_location=True)
    large_rows, large = _measure(client, count_queries, "/equipment/accessible", token)

    # Not decoration. Without it, a scoping regression that empties the listing
    # satisfies the query assertion below perfectly.
    assert large_rows > small_rows, (
        f"the listing did not actually grow ({small_rows} -> {large_rows}); "
        "the query assertion below would be vacuous"
    )
    assert large.count == small.count, (
        f"query count grew with row count: {small.count} statements for "
        f"{small_rows} rows, {large.count} for {large_rows}. "
        f"users={len(large.against('FROM users'))} "
        f"catalog={len(large.against('FROM catalog_items'))} "
        f"locations={len(large.against('FROM locations'))}"
    )


def test_my_equipment_does_not_fan_out(
    client, db_session, group_graph, mock_matrix_db, count_queries
):
    """GET /users/me/equipment.

    Only catalog_item and owner can fan out here, and that is structural rather
    than an oversight in this test: the route filters on holder == me, so the
    holder is one row the session loads once, and `location` is unreachable
    because the holder branch of current_state_description wins ahead of it.
    Do not add assertions for those two expecting them to bite.
    """
    mine = mock_matrix_db["soldier_a"].id
    token = create_auth_header("u_soldier_a")
    company_a = group_graph["188/53/A"]

    _stock(db_session, company_a, 2, tag="mine", start=0, holder_id=mine)
    small_rows, small = _measure(client, count_queries, "/users/me/equipment", token)

    _stock(db_session, company_a, 6, tag="mine", start=100, holder_id=mine)
    large_rows, large = _measure(client, count_queries, "/users/me/equipment", token)

    assert large_rows > small_rows
    assert large.count == small.count, (
        f"{small.count} statements for {small_rows} rows, "
        f"{large.count} for {large_rows}"
    )


def test_verification_history_does_not_fan_out(
    client, db_session, group_graph, mock_matrix_db, count_queries
):
    """GET /verifications/equipment/{id} -- reporter_name reads v.reporter."""
    token = create_auth_header("u_cmdr_a")
    item = db_session.query(models.Equipment).filter(
        models.Equipment.serial_number == "SA100"
    ).one()
    url = f"/verifications/equipment/{item.id}"

    _stock_history(db_session, item.id, 2, start=0)
    small_rows, small = _measure(client, count_queries, url, token)

    _stock_history(db_session, item.id, 6, start=100)
    large_rows, large = _measure(client, count_queries, url, token)

    assert large_rows > small_rows
    assert large.count == small.count, (
        f"{small.count} statements for {small_rows} verifications, "
        f"{large.count} for {large_rows}; "
        f"users={len(large.against('FROM users'))}"
    )


def test_status_history_does_not_fan_out(
    client, db_session, group_graph, mock_matrix_db, count_queries
):
    """GET /equipment/{id}/history -- user_name reads h.user."""
    token = create_auth_header("u_cmdr_a")
    item = db_session.query(models.Equipment).filter(
        models.Equipment.serial_number == "SA100"
    ).one()
    url = f"/equipment/{item.id}/history"

    _stock_history(db_session, item.id, 2, start=200)
    small_rows, small = _measure(client, count_queries, url, token)

    _stock_history(db_session, item.id, 6, start=300)
    large_rows, large = _measure(client, count_queries, url, token)

    assert large_rows > small_rows
    assert large.count == small.count, (
        f"{small.count} statements for {small_rows} history rows, "
        f"{large.count} for {large_rows}; "
        f"users={len(large.against('FROM users'))}"
    )


# --- what eager loading could break that fanning out did not ---------------

def test_an_orphaned_item_is_still_listed(
    client, db_session, group_graph, mock_matrix_db, count_queries
):
    """An item with no holder, no owner and no location must survive the joins.

    This is the failure mode eager loading introduces and lazy loading cannot:
    joinedload defaults to a LEFT OUTER JOIN, but innerjoin=True is a plausible
    thing for someone to add later while "optimising", and every one of these
    relationships is nullable. Under an inner join this row silently vanishes
    from the listing -- no error, just one fewer item -- and the item that
    belongs to nobody and sits nowhere is precisely the one somebody needs to
    see. DATA-M10 makes the same complaint about the filter joins one file over.

    Asserted through the API rather than by reading SQL so it keeps meaning if
    the implementation changes.
    """
    token = create_auth_header("u_cmdr_a")
    company_a = group_graph["188/53/A"]

    cat = models.CatalogItem(name="orphan-cat")
    db_session.add(cat)
    db_session.flush()
    db_session.add(models.Equipment(
        catalog_item_id=cat.id, status="Functional", group_id=company_a.id,
        holder_user_id=None, owner_user_id=None,
        actual_location_id=None, custom_location=None,
        sensitivity="UNCLASSIFIED", serial_number="ORPHAN1",
    ))
    db_session.commit()

    response = client.get("/equipment/accessible", headers=token)
    assert response.status_code == 200, response.text
    serials = [row["serial_number"] for row in response.json()]
    assert "ORPHAN1" in serials, (
        "the orphaned item vanished from the listing -- an eager load became an "
        f"inner join. Listed: {serials}"
    )


def test_location_is_live_on_my_equipment_despite_the_holder_filter(
    client, db_session, group_graph, mock_matrix_db, count_queries
):
    """Do not narrow EQUIPMENT_RESPONSE_LOADS for /users/me/equipment.

    The tempting argument, and it is wrong: that route filters on holder == me,
    so the holder branch of current_state_description always wins and `location`
    can never be read -- therefore two of the four loads are dead weight there
    and the route should get a narrower tuple.

    It misreads the property. Equipment.current_state_description returns early
    only when holder and owner DIFFER. For kit you both hold and own -- the
    ordinary case on a "my equipment" page -- that test is false, control falls
    through to its location branch, and `self.location` IS read. Narrowing the
    tuple would
    reintroduce exactly the fan-out this ticket closed, on the most common row
    shape the route serves.

    So this test stocks that shape and holds the count flat. It exists to fail
    for anyone who follows the reasoning above.
    """
    mine = mock_matrix_db["soldier_a"].id
    token = create_auth_header("u_soldier_a")
    company_a = group_graph["188/53/A"]

    def stock_owned_held_and_stored(n, start):
        for i in range(start, start + n):
            catalog = models.CatalogItem(name=f"selfheld-cat-{i}")
            where = models.Location(name=f"selfheld-loc-{i}", type="Armory")
            db_session.add_all([catalog, where])
            db_session.flush()
            db_session.add(models.Equipment(
                catalog_item_id=catalog.id, status="Functional",
                group_id=company_a.id,
                holder_user_id=mine, owner_user_id=mine,   # the same person
                actual_location_id=where.id, custom_location=None,
                sensitivity="UNCLASSIFIED", serial_number=f"SELF{i}",
            ))
        db_session.commit()

    stock_owned_held_and_stored(2, 0)
    small_rows, small = _measure(client, count_queries, "/users/me/equipment", token)

    stock_owned_held_and_stored(6, 100)
    large_rows, large = _measure(client, count_queries, "/users/me/equipment", token)

    assert large_rows > small_rows
    assert large.count == small.count, (
        f"{small.count} statements for {small_rows} rows, {large.count} for "
        f"{large_rows}. If EQUIPMENT_RESPONSE_LOADS was just narrowed for this "
        f"route, that is the cause -- read this test's docstring. "
        f"locations={len(large.against('FROM locations'))}"
    )


def test_the_text_filter_still_matches_and_still_does_not_fan_out(
    client, db_session, group_graph, mock_matrix_db, count_queries
):
    """?query_str= adds its own join(CatalogItem) beside the joinedload.

    Two joins to the same table in one statement. It works because joinedload
    emits an anonymous alias rather than reusing the explicit join -- the same
    thing reports.py:45 already depends on -- but "already depends on" is not
    evidence about THIS query, so both halves are checked here: that the filter
    still selects the right rows, and that adding the alias did not reintroduce
    a per-row load on the filtered path.
    """
    token = create_auth_header("u_cmdr_a")
    company_a = group_graph["188/53/A"]

    _stock(db_session, company_a, 8, tag="thermal")

    with count_queries() as narrow:
        one = client.get("/equipment/accessible?query_str=thermal-cat-3", headers=token)
    with count_queries() as broad:
        many = client.get("/equipment/accessible?query_str=thermal-cat", headers=token)

    assert one.status_code == 200 and many.status_code == 200
    assert [r["serial_number"] for r in one.json()] == ["THERMAL3"], (
        f"the filter stopped selecting correctly: {one.json()}"
    )
    assert len(many.json()) == 8, f"expected all 8 thermal items, got {len(many.json())}"
    assert broad.count == narrow.count, (
        f"the filtered path fans out: {narrow.count} statements for 1 row, "
        f"{broad.count} for 8"
    )


@pytest.mark.parametrize(
    "url_template, expected_rows",
    [
        # The fixture gives company_cmdr_a exactly the two Company A items.
        ("/equipment/accessible", 2),
        ("/verifications/equipment/{item_id}", 0),
        ("/equipment/{item_id}/history", 0),
    ],
)
def test_empty_and_single_row_responses_are_unaffected(
    client, db_session, group_graph, mock_matrix_db, url_template, expected_rows
):
    """The degenerate row counts, which no scaling assertion ever visits.

    A listing of zero and a listing of one are where an eager-loaded query is
    most likely to differ from a lazy one -- an empty outer join, a single row
    with every relationship null -- and both history routes start empty for
    every item in the fixture.
    """
    token = create_auth_header("u_cmdr_a")
    item = db_session.query(models.Equipment).filter(
        models.Equipment.serial_number == "SA100"
    ).one()

    response = client.get(url_template.format(item_id=item.id), headers=token)
    assert response.status_code == 200, response.text
    assert len(response.json()) == expected_rows


# --- the meta-test: does any of the above have teeth? -----------------------

@pytest.mark.parametrize(
    "target, url, subject, holds_everything",
    [
        (
            "backend.routers.equipment.EQUIPMENT_RESPONSE_LOADS",
            "/equipment/accessible", "u_cmdr_a", False,
        ),
        (
            "backend.routers.users.EQUIPMENT_RESPONSE_LOADS",
            "/users/me/equipment", "u_soldier_a", True,
        ),
    ],
)
def test_the_counter_can_actually_see_a_fanout(
    client, db_session, group_graph, mock_matrix_db, count_queries, monkeypatch,
    target, url, subject, holds_everything,
):
    """Strip the loads back out and prove the count explodes.

    Every other test in this file asserts a NEGATIVE -- that a number does not
    grow -- and a negative passes just as happily when the measurement is broken
    as when the code is right. This is the positive control that says the
    instrument is live.

    It is the DATA-H7 lesson in a different shape. There, a second safety layer
    masked defects in the first and three killed mutations came back to life,
    caught only by re-running the mutations after adding the layer. Here the
    masking agent is the ORM's cache rather than a constraint, and the same
    discipline applies: assert from inside that the two states are actually
    distinguishable, rather than trusting that they must be.

    Stripping the tuple in the ROUTER's namespace, not in dependencies, because
    each router imported the name at module load; rebinding the source would
    leave both routes holding the original tuple and this test would report a
    fan-out that never happened.

    If this ever fails, do not delete it. Three things could be true, and they
    want opposite responses: the ORM started eager-loading at the model level
    (the .options() are redundant -- revisit DATA-H8 rather than re-pin it);
    DATA-M19 extracted the shared response builder and this tuple stopped being
    an importable module-level name (repoint the patch, the coupling is this
    test's, not the code's); or the measurement broke and every other assertion
    in this file is worthless.
    """
    token = create_auth_header(subject)
    holder_id = mock_matrix_db["soldier_a"].id if holds_everything else None
    _stock(
        db_session, group_graph["188/53/A"], 8, tag="control", holder_id=holder_id,
    )

    _, fixed = _measure(client, count_queries, url, token)

    monkeypatch.setattr(target, ())
    _, stripped = _measure(client, count_queries, url, token)

    assert stripped.count > fixed.count * 3, (
        f"stripping {target} changed the query count from {fixed.count} to "
        f"{stripped.count} -- barely, or not at all. The fan-out measurement "
        f"is not sensitive, so every other assertion in this file is vacuous."
    )


@pytest.mark.parametrize(
    "attribute, url_template",
    [
        ("reporter", "/verifications/equipment/{item_id}"),
        ("user", "/equipment/{item_id}/history"),
    ],
)
def test_the_counter_can_see_a_fanout_on_the_history_routes(
    client, db_session, group_graph, mock_matrix_db, count_queries, monkeypatch,
    attribute, url_template,
):
    """The same positive control for the two history routes.

    Added because the control above covered only the two equipment listings,
    leaving half of DATA-H8's routes asserted by a bare negative -- and a bare
    negative passes just as happily when the instrument is dead. These two
    resolve the item through get_scoped_equipment_or_404 first, which leaves
    the session in a different state than the listings do, so "it must behave
    the same" is exactly the assumption worth not making.

    Patched by swapping the router's `joinedload` for `lazyload` on the one
    relationship under test -- these routes name the loader inline, so there is
    no importable constant to strip, and lazyload is joinedload's exact inverse
    rather than an approximation of "unfixed".
    """
    from backend.routers import verifications as module

    token = create_auth_header("u_cmdr_a")
    item = db_session.query(models.Equipment).filter(
        models.Equipment.serial_number == "SA100"
    ).one()
    url = url_template.format(item_id=item.id)

    _stock_history(db_session, item.id, 8, start=500)
    _, fixed = _measure(client, count_queries, url, token)

    real = module.joinedload

    def inert(target):
        if target.key == attribute:
            return sqlalchemy.orm.lazyload(target)
        return real(target)

    monkeypatch.setattr(module, "joinedload", inert)
    _, stripped = _measure(client, count_queries, url, token)

    assert stripped.count > fixed.count * 3, (
        f"neutering {attribute} changed the query count from {fixed.count} to "
        f"{stripped.count} -- barely, or not at all. This route's fan-out test "
        f"is not measuring anything. users={len(stripped.against('FROM users'))}"
    )
