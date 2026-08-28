"""
Behavioural coverage for H1-3 (TODO-SEC-H1.md) -- the query surface.

descendant_ids() and extent() are the point at which the group algebra becomes
askable, and primary_group_id() asks the inverse question H1-6 needs -- not what
a user may reach but where they sit. Everything here tests the expressions
directly, below the routes that now compose them.

Three properties get disproportionate attention, because each is a defect this
whole replacement exists to remove rather than an ordinary edge case:

  * The answer must be a SET. Ancestors and grants overlap constantly, and a
    duplicated id silently multiplies the result set of whatever query the
    expression is composed into.
  * The answer must be a Select, built without touching the database. If these
    materialised ids instead, every scoped request would pay a round trip to
    fetch them and ship them back as an IN (...) literal.
  * The capability must be a bound parameter, never interpolated text. SEC-H1
    exists because dependencies.py:88 compiles startswith() into an unescaped
    LIKE, so a stored "%" reads the whole force. The wildcard has to be inert
    here.

Nothing in this module consults Profile, User.role or a path string. Visibility
is not a special case; it is may(P, VIEW, R).
"""
import contextlib

import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import Select

from backend import authz, models
from backend.enums import Capability

# --- helpers --------------------------------------------------------------

class RawCapability:
    """Stands in for a Capability member carrying an attacker-chosen value.

    Grant.capability is a plain String, so the column accepts values the enum
    never will. That is the point: the hostile-input tests below need "%" and
    "' OR '1'='1" to be writable and queryable, and no enum member should
    ever exist for them.

    H1-7 added TRANSFER and REPORT_STATUS, so this is no longer how a second
    real verb is expressed -- only how an unreal one is.
    """

    def __init__(self, value):
        self.value = value


@contextlib.contextmanager
def recorded(engine):
    """Collect every SQL statement the block actually sends to the database."""
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


@pytest.fixture
def org(db_session):
    """A small force, and users whose grants sit at different heights.

                  Bde188
                 /      \\
              Bn53      Bn71
             /    \\        \\
          CoA     CoB      CoZ

    brigade   - VIEW on the root
    company   - VIEW on one leaf
    split     - VIEW on CoA and CoZ, which contain each other not at all
    overlap   - VIEW on Bn53 AND on CoA, which Bn53 already contains
    ungranted - nothing
    """
    names = ["Bde188", "Bn53", "Bn71", "CoA", "CoB", "CoZ"]
    groups = {name: authz.Unit(name=name) for name in names}
    db_session.add_all(groups.values())
    db_session.flush()
    for parent, child in [
        ("Bde188", "Bn53"), ("Bde188", "Bn71"),
        ("Bn53", "CoA"), ("Bn53", "CoB"), ("Bn71", "CoZ"),
    ]:
        authz.add_edge(db_session, groups[parent].id, groups[child].id)

    users = {
        name: models.User(personal_number=name, full_name=name)
        for name in ["brigade", "company", "split", "overlap", "ungranted"]
    }
    db_session.add_all(users.values())
    db_session.flush()

    for who, where in [
        ("brigade", "Bde188"),
        ("company", "CoA"),
        ("split", "CoA"), ("split", "CoZ"),
        ("overlap", "Bn53"), ("overlap", "CoA"),
    ]:
        db_session.add(authz.Grant(
            user_id=users[who].id,
            group_id=groups[where].id,
            capability=Capability.VIEW.value,
        ))
    db_session.flush()

    groups["_users"] = users
    return groups


def names_of(db, org, statement):
    """Resolve a selectable to sorted group names, refusing duplicates.

    The duplicate assertion is not incidental. desc(G) and extent(P,C) are sets
    in the model, and collapsing a doubled row here would hide the one defect
    that costs the most later -- a scoped query returning each item twice.
    """
    reverse = {group.id: name for name, group in org.items() if name != "_users"}
    rows = db.execute(statement).scalars().all()
    assert len(rows) == len(set(rows)), f"duplicate ids: {sorted(rows)}"
    return sorted(reverse[row] for row in rows)


def extent_of(db, org, who, capability=Capability.VIEW):
    return names_of(db, org, authz.extent(org["_users"][who].id, capability))


# --- the three Done-when clauses ------------------------------------------

def test_grants_on_two_disjoint_groups_union(db_session, org):
    """`split` holds CoA and CoZ, which share only the root above them."""
    assert extent_of(db_session, org, "split") == ["CoA", "CoZ"]


def test_a_grant_on_a_leaf_yields_exactly_that_leaf(db_session, org):
    """Not its siblings, not its battalion, not the group's own parent."""
    assert extent_of(db_session, org, "company") == ["CoA"]


def test_a_view_grant_is_absent_from_another_capabilitys_extent(db_session, org):
    """Capabilities do not leak into one another.

    H1-7 added the real verbs, so this says TRANSFER rather than fabricating
    it. The claim is unchanged and the reading is not: a grant carrying one
    verb is invisible to every query asking about another.
    """
    transfer = Capability.TRANSFER
    db_session.add(authz.Grant(
        user_id=org["_users"]["company"].id,
        group_id=org["Bde188"].id,
        capability=transfer.value,
    ))
    db_session.flush()

    # The new grant is real under its own capability...
    assert extent_of(db_session, org, "company", transfer) == [
        "Bde188", "Bn53", "Bn71", "CoA", "CoB", "CoZ"
    ]
    # ...and invisible under VIEW, which still sees only the leaf.
    assert extent_of(db_session, org, "company") == ["CoA"]


# --- authority is positional ----------------------------------------------

def test_a_grant_on_the_root_yields_the_whole_force(db_session, org):
    """The same VIEW grant means one company on a leaf and everything here.

    This is the property that lets H1-10 collapse three profile booleans, a
    five-member role enum and four path columns into where a grant sits.
    """
    assert extent_of(db_session, org, "brigade") == [
        "Bde188", "Bn53", "Bn71", "CoA", "CoB", "CoZ"
    ]


def test_two_users_extents_do_not_bleed(db_session, org):
    """Grants are per-user; nothing about `brigade` widens `company`."""
    assert extent_of(db_session, org, "company") == ["CoA"]
    assert extent_of(db_session, org, "ungranted") == []


# --- the answer is a set --------------------------------------------------

def test_overlapping_grants_yield_each_group_once(db_session, org):
    """`overlap` holds Bn53 and CoA, and Bn53 already contains CoA.

    Both grants route to CoA's subtree, so the join produces it twice. Under
    IN (subquery) that is invisible; composed as a JOIN it doubles rows.
    """
    rows = db_session.execute(
        authz.extent(org["_users"]["overlap"].id, Capability.VIEW)
    ).scalars().all()

    assert len(rows) == 3, "an overlapping grant contributed a duplicate id"
    assert extent_of(db_session, org, "overlap") == ["Bn53", "CoA", "CoB"]


def test_descendant_ids_over_overlapping_ancestors_yields_each_group_once(db_session, org):
    """desc() of a set of groups is a set.

    Regression: distinct() first sat on extent() alone, so this returned Bn53's
    subtree twice -- once via Bde188 and once via Bn53 -- and extent() masked it.
    The first caller to use descendant_ids() directly would have inherited the
    duplication silently.
    """
    rows = db_session.execute(
        authz.descendant_ids([org["Bde188"].id, org["Bn53"].id])
    ).scalars().all()

    assert len(rows) == 6
    assert names_of(db_session, org, authz.descendant_ids([org["Bde188"].id, org["Bn53"].id])) == [
        "Bde188", "Bn53", "Bn71", "CoA", "CoB", "CoZ"
    ]


def test_descendant_ids_accepts_a_list_or_a_select_interchangeably(db_session, org):
    """The property that lets extent() compose this rather than rejoin by hand."""
    as_list = authz.descendant_ids([org["Bn53"].id])
    as_select = authz.descendant_ids(
        select(authz.Group.id).where(authz.Group.name == "Bn53")
    )

    assert names_of(db_session, org, as_list) == ["Bn53", "CoA", "CoB"]
    assert names_of(db_session, org, as_select) == ["Bn53", "CoA", "CoB"]


def test_descendant_ids_of_nothing_is_nothing(db_session, org):
    """An empty IN must yield no rows, not every row and not a SQL error."""
    assert names_of(db_session, org, authz.descendant_ids([])) == []


# --- fail closed ----------------------------------------------------------

def test_a_user_with_no_grant_sees_nothing_rather_than_everything(db_session, org):
    """The direction this must fail in. An empty extent is an empty IN."""
    assert extent_of(db_session, org, "ungranted") == []


def test_a_grant_on_a_group_deleted_since_yields_nothing(db_session, org):
    """Deleting a group revokes the authority held over it, by cascade.

    Two independent mechanisms have to agree here. grants.group_id declares
    ondelete=CASCADE, which backend.database's PRAGMA foreign_keys makes real
    on SQLite, so the Grant rows go with the group -- both of `company`'s and
    both of the ones `split` and `overlap` hold on CoA. And H1-2 leaves no
    closure rows for a dead group, so even a grant that somehow survived would
    name nothing.
    """
    db_session.delete(org["CoA"])
    authz.rebuild_closure(db_session)

    assert db_session.query(authz.Grant).count() == 3, "the cascade left grants behind"
    assert extent_of(db_session, org, "company") == []
    # The grants those users hold elsewhere are untouched.
    assert extent_of(db_session, org, "split") == ["CoZ"]
    assert extent_of(db_session, org, "overlap") == ["Bn53", "CoB"]


def test_a_grant_yields_nothing_until_the_closure_is_built(db_session, org):
    """Groups and grants alone decide nothing; the closure is what is queried.

    Worth pinning for H1-4, which seeds the graph: a seed that writes groups,
    edges and grants but never calls rebuild_closure leaves every non-master
    with an empty inventory, and fails closed rather than loudly.
    """
    db_session.query(authz.GroupClosure).delete()
    db_session.flush()

    assert extent_of(db_session, org, "brigade") == []

    authz.rebuild_closure(db_session)
    assert extent_of(db_session, org, "brigade") == [
        "Bde188", "Bn53", "Bn71", "CoA", "CoB", "CoZ"
    ]


# --- hostile input: the defect class that motivated the replacement -------

def test_a_stored_wildcard_capability_matches_nothing(db_session, org):
    """The exact shape of the defect SEC-H1 replaces rather than patches.

    dependencies.py:88 compiles startswith() to an unescaped LIKE, so a user
    stored with unit_hierarchy = "%" reads the whole force. Here the comparison
    is equality against a bound parameter, so "%" is just a string nobody
    granted.
    """
    db_session.add(authz.Grant(
        user_id=org["_users"]["ungranted"].id,
        group_id=org["Bde188"].id,
        capability="%",
    ))
    db_session.flush()

    assert extent_of(db_session, org, "ungranted") == []
    assert extent_of(db_session, org, "ungranted", RawCapability("%")) == [
        "Bde188", "Bn53", "Bn71", "CoA", "CoB", "CoZ"
    ], "the row exists; only the VIEW query declines to match it"


@pytest.mark.parametrize(
    "hostile",
    ["' OR '1'='1", "VIEW' --", "%", "_IEW", "VIEW\x00"],
)
def test_a_hostile_capability_is_a_bound_parameter_not_sql(db_session, org, hostile):
    """None of these may widen an extent, and none may raise a SQL error."""
    assert extent_of(db_session, org, "brigade", RawCapability(hostile)) == []


def test_a_plain_string_capability_is_refused_loudly(org):
    """Better an AttributeError than a silently empty extent.

    extent() reads capability.value, so a caller passing "VIEW" fails at once.
    Accepting the bare string would work today only because Capability
    subclasses str, and would quietly return nothing for any typo.
    """
    with pytest.raises(AttributeError):
        authz.extent(org["_users"]["brigade"].id, "VIEW")


# --- the reason these return Selects --------------------------------------

def test_building_an_extent_touches_no_database(db_session, org):
    """A Select is an expression, which is why neither function takes a Session."""
    with recorded(db_session.get_bind()) as statements:
        built = authz.extent(org["_users"]["brigade"].id, Capability.VIEW)

    assert isinstance(built, Select)
    assert statements == [], f"building the expression issued SQL: {statements}"


def test_the_extent_scopes_a_query_in_a_single_statement(db_session, org):
    """H1-5's dry run, and the whole reason H1-3 returns selectables.

    Materialised ids would cost a round trip to fetch them plus an IN (...)
    literal carrying one bind per visible group. Composed as a subquery, the
    database answers the entire question once.
    """
    catalog = models.CatalogItem(name="Radio")
    db_session.add(catalog)
    db_session.flush()
    for serial, group in [("SN-A", "CoA"), ("SN-B", "CoB"), ("SN-Z", "CoZ")]:
        db_session.add(models.Equipment(
            serial_number=serial,
            catalog_item_id=catalog.id,
            status="Functional",
            group_id=org[group].id,
        ))
    db_session.flush()

    scoped = db_session.query(models.Equipment).filter(
        models.Equipment.group_id.in_(
            authz.extent(org["_users"]["company"].id, Capability.VIEW)
        )
    )

    with recorded(db_session.get_bind()) as statements:
        visible = scoped.all()

    assert [item.serial_number for item in visible] == ["SN-A"]
    assert len(statements) == 1, f"scoping cost {len(statements)} round trips: {statements}"
    assert "group_closure" in statements[0], "the closure join was not pushed into the query"


def test_the_statement_compiles_on_postgres():
    """CI migrates on Postgres while the suite runs on SQLite.

    A subquery-in-IN with DISTINCT is portable, but nothing else in this suite
    would notice if that stopped being true.
    """
    compiled = str(
        authz.extent(1, Capability.VIEW).compile(dialect=postgresql.dialect())
    )
    collapsed = " ".join(compiled.split())

    assert "SELECT DISTINCT" in collapsed
    assert "group_closure" in collapsed and "grants" in collapsed
    # The capability arrives as a bind parameter, not as inlined text.
    assert "VIEW" not in collapsed


# --- primary_group_id ------------------------------------------------------
#
# H1-6 needs the inverse question of the two above: not "what may this user
# reach?" but "where does this user sit?", so an equipment write can stamp a
# group on the item. Membership answers it, and membership is many-to-many, so
# the answer is a rule rather than a lookup.


def sits_in(db, org, who, *where):
    db.add_all([
        authz.GroupMembership(user_id=org["_users"][who].id, group_id=org[name].id)
        for name in where
    ])
    db.flush()


def group_of(db, org, who):
    """primary_group_id as a name, so failures read as places not ids."""
    reverse = {group.id: name for name, group in org.items() if name != "_users"}
    group_id = authz.primary_group_id(db, org["_users"][who].id)
    return None if group_id is None else reverse[group_id]


def test_a_user_who_is_a_member_of_nothing_has_no_group(db_session, org):
    """None, not a default and not an exception.

    The write paths decide what to do about it, and they no longer agree --
    which is the argument for answering None here rather than picking a
    fallback. Creation refuses outright (require() denies a None group, so the
    caller gets a 403); transfer leaves the item exactly where it was. A
    fallback invented in this function would take that decision away from the
    only code with the context to make it, and would quietly place equipment
    somewhere nobody chose.

    This used to justify itself by Equipment.group_id being nullable. H1-11
    made that column NOT NULL; the None still originates here, from a user who
    is a member of nothing, and that is exactly why it must not be invented.
    """
    assert group_of(db_session, org, "ungranted") is None


def test_one_membership_is_the_group(db_session, org):
    sits_in(db_session, org, "company", "CoA")
    assert group_of(db_session, org, "company") == "CoA"


def test_the_most_specific_membership_wins(db_session, org):
    """A user sitting at three heights puts equipment at the lowest one.

    The alternative -- picking the brigade because it came back first -- would
    place a company's equipment where every commander in the force can see it.
    Over-grant by row order.
    """
    sits_in(db_session, org, "overlap", "Bde188", "Bn53", "CoA")
    assert group_of(db_session, org, "overlap") == "CoA"


def test_the_most_specific_membership_wins_regardless_of_insertion_order(db_session, org):
    """The same three memberships written deepest-first.

    Pinned separately because both the min() tie-break and the containment
    filter could be accidentally satisfied by insertion order in the test above,
    and neither is allowed to depend on it.
    """
    sits_in(db_session, org, "overlap", "CoA", "Bn53", "Bde188")
    assert group_of(db_session, org, "overlap") == "CoA"


def test_incomparable_memberships_resolve_deterministically(db_session, org):
    """CoA and CoZ contain each other not at all, so this is a real choice.

    What matters is not which one wins but that the same user gets the same
    answer every time: the alternative is an item that changes unit between two
    identical requests, and a commander who sees it in one listing and not the
    next.
    """
    sits_in(db_session, org, "split", "CoA", "CoZ")

    answers = {group_of(db_session, org, "split") for _ in range(5)}
    assert len(answers) == 1
    assert answers.pop() in {"CoA", "CoZ"}


def test_a_grant_is_not_a_membership(db_session, org):
    """The brigade commander holds VIEW over the whole force and sits in CoA.

    Authority and location are different facts. Deriving the group from the
    grant instead would put every item this user touches at the root, visible
    to the entire force -- exactly the conflation the model exists to end.
    """
    sits_in(db_session, org, "brigade", "CoA")
    assert extent_of(db_session, org, "brigade") == ["Bde188", "Bn53", "Bn71", "CoA", "CoB", "CoZ"]
    assert group_of(db_session, org, "brigade") == "CoA"


def test_two_users_memberships_do_not_bleed(db_session, org):
    sits_in(db_session, org, "company", "CoA")
    sits_in(db_session, org, "split", "CoZ")

    assert group_of(db_session, org, "company") == "CoA"
    assert group_of(db_session, org, "split") == "CoZ"
    assert group_of(db_session, org, "ungranted") is None


def test_a_group_outside_the_closure_does_not_displace_a_real_membership(db_session, org):
    """The sharp edge, bounded rather than fixed.

    A group written without rebuild_closure has no closure rows at all, so it
    appears in no container set and stays a candidate forever -- it can never be
    ruled out by containment, because it contains nothing and nothing contains
    it. An item stamped with such a group is visible to nobody, since even a
    direct grant over it resolves to the empty set.

    What bounds the damage is that it cannot WIN by existing; only the tie-break
    can pick it. Here it loses, and the assertion above the result says exactly
    why: it was created last and holds the higher id. Reverse those ids -- a
    stranded group created before the real one -- and it would win, and nothing
    in primary_group_id would stop it. That is the whole of the edge, and the
    fix belongs wherever groups are created, not here.
    """
    stranded = authz.Unit(name="Stranded")
    db_session.add(stranded)
    db_session.flush()
    db_session.add(authz.GroupMembership(
        user_id=org["_users"]["company"].id, group_id=stranded.id
    ))
    sits_in(db_session, org, "company", "CoA")

    assert db_session.query(authz.GroupClosure).filter_by(ancestor_id=stranded.id).count() == 0
    assert stranded.id > org["CoA"].id, "precondition: the tie-break below is min(id)"
    assert group_of(db_session, org, "company") == "CoA"


# --- the derived-VIEW rule ---------------------------------------------------


def test_implied_view_covers_every_equipment_verb():
    """The rule H1-10.5 replaced two hand-placed judgements with.

    Asserted against the function rather than against either grant table,
    because both tables satisfy it by construction now -- the seed and the
    fixture call this to build their VIEW rows. A test reading those tables
    would be asserting that the derivation derived what it derived.
    """
    placements = {
        Capability.TRANSFER: [("cmdr", "188/53/A")],
        Capability.CREATE_EQUIPMENT: [("tech", "188/53/A")],
        Capability.REPORT_STATUS: [("tech", "188/53/A"), ("bn", "188/53")],
        Capability.RESOLVE_FAULT: [("bn", "188/53")],
    }
    assert authz.implied_view_placements(placements) == {
        ("cmdr", "188/53/A"),
        ("tech", "188/53/A"),
        ("bn", "188/53"),
    }


def test_a_global_verb_implies_no_sight_of_equipment():
    """The exclusion, which no grant table can demonstrate.

    MANAGE_CATALOG and MANAGE_PERSONNEL authorise vocabulary and people, not
    equipment, so they must not imply VIEW. Fold them in and anyone who may
    create a user can see every item in the force -- they sit on the root, so
    the implied grant would be the widest one the model can express.

    This exists because mutation found the exclusion unobservable: every seeded
    holder of a global verb also holds root VIEW for independent reasons, so
    adding them to the rule changed nothing in either table. The only way to
    reach it is to ask the rule directly about an account that holds a global
    verb and nothing else.
    """
    placements = {
        Capability.MANAGE_CATALOG: [("quartermaster", "188")],
        Capability.MANAGE_PERSONNEL: [("adjutant", "188")],
    }
    assert authz.implied_view_placements(placements) == set()


def test_the_rule_ignores_a_capability_it_was_not_given():
    """VIEW itself is not an input, and neither is anything unknown.

    Passing VIEW back in would be harmless but circular; the callers union the
    literal VIEW table with this result, so the rule only ever answers "what
    else must be visible".
    """
    assert authz.implied_view_placements({}) == set()
    assert authz.implied_view_placements(
        {Capability.VIEW: [("cmdr", "188")]}
    ) == set()
