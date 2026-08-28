"""
The group algebra: the access model's single source of truth.

The system only ever needs to answer one question — may principal P exercise
capability C over resource R? This module holds the tables that answer it:

    desc(G)      = { H : G contains H, transitively }   G itself included
    extent(P,C)  = union of desc(g.group) for each grant g of P carrying C
    may(P,C,R)   = group(R) is in extent(P,C)

Visibility is not a special case of that — it is may(P, VIEW, R). Extent and
authority are the same mechanism, which is what lets the four legacy path
representations and the profile permission matrix collapse into one relation.

Containment is a DAG, not a tree: a company seconded to a task force has two
parents, and that is ordinary. Ancestry is materialised in group_closure so
desc(G) is an indexed join rather than a string prefix match.

Deliberately free of any dependency on models.py — every foreign key is
declared by table-name string, so the two modules can be imported in either
order. models.py imports this one purely to register these tables with
Base.metadata.

Every ondelete rule below is real on both dialects. Postgres enforces foreign
keys unconditionally; SQLite ignores them entirely unless PRAGMA
foreign_keys=ON, which database.py sets on every connection it hands out.
Deleting a Group therefore takes its edges, closure rows, memberships and
grants with it. Pinned by tests/test_group_schema.py, which also pins the one
engine deliberately exempt: migrations, because Alembic's batch mode cannot
run under enforcement.

This module carries the schema (H1-1), the closure engine that maintains it
(H1-2), the query surface that asks it questions (H1-3), and the gate that
answers may(P,C,R) and raises 403 when the answer is no (H1-7).

That gate imports HTTPException, which couples the algebra to the web layer
and is a real cost. It is paid because the alternative -- a neutral exception
that every router translates for itself -- is precisely the drift this model
exists to end: DATA-H9 was two copies of one scoping rule, and a per-router
translation is the same defect wearing a different hat. One decision, one
place, one status code.

CycleError below is graphlib's, re-exported rather than defined here: this
codebase has no custom exception classes, and graphlib.CycleError already
subclasses ValueError, the builtin security.py:16 raises to refuse.

Two invariants the engine maintains, both easy to break silently. Closure
`depth` is the SHORTEST path between the pair. And the closure is rebuilt
wholesale from group_edges rather than maintained incrementally -- incremental
DAG-closure maintenance is the classic defect source in this pattern and buys
nothing at this scale: a rebuild is 8.5 ms at 50 groups, 216 ms at 1000
(branching-4 tree, in-memory SQLite). Past roughly 1000 groups or 10 000
closure rows, revisit; the limiter is closure row count, not the traversal.
"""
from collections import defaultdict, deque
from collections.abc import Iterable
from graphlib import CycleError, TopologicalSorter

from fastapi import HTTPException
from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    literal,
    select,
)
from sqlalchemy.orm import Session, relationship
from sqlalchemy.sql import Select

from .database import Base
from .enums import Capability, GroupKind


class Group(Base):
    """A set of principals and resources. The only unit of encapsulation.

    Polymorphic on `kind`, but the algebra never branches on it: any Group
    substitutes for any other in scoping, so adding a kind must not require a
    change to desc(), extent() or may(). An `if kind == ...` on a scoping path
    is a design error, not a special case.
    """
    __tablename__ = 'groups'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True, index=True)
    kind = Column(String, nullable=False)

    # No polymorphic_identity on the base, deliberately. Giving it one lets a
    # bare Group() persist kind='GROUP' -- a value outside GroupKind, which
    # would make GroupKind(row.kind) unsafe for later code. Without one the
    # base cannot be stored at all: kind comes out NULL and the NOT NULL
    # constraint rejects it.
    #
    # That is an ORM-level guarantee only. `kind` is a plain String with no
    # CHECK, so raw SQL, bulk_insert_mappings or a seed can still write
    # anything -- the suite itself does, in the raw-SQL fixtures below. H1-7
    # is where the vocabulary settles and a CHECK becomes worth adding.
    __mapper_args__ = {"polymorphic_on": kind}


class Unit(Group):
    """A standing formation — brigade, battalion, company."""
    __mapper_args__ = {"polymorphic_identity": GroupKind.UNIT.value}


class TaskForce(Group):
    """A temporary grouping that cuts across the standing hierarchy.

    The reason containment is a DAG: a company attached to a task force keeps
    its parent battalion and gains a second parent. A tree cannot say that.
    """
    __mapper_args__ = {"polymorphic_identity": GroupKind.TASK_FORCE.value}


class GroupEdge(Base):
    """Direct containment: `parent` contains `child`.

    The authoritative structure. group_closure is derived from these rows and
    must never be written independently of them. The digraph has to stay
    acyclic — antisymmetry is what makes containment a partial order rather
    than merely a relation — which H1-2 enforces on insert.
    """
    __tablename__ = 'group_edges'

    parent_id = Column(Integer, ForeignKey('groups.id', ondelete='CASCADE'), primary_key=True)
    child_id = Column(Integer, ForeignKey('groups.id', ondelete='CASCADE'), primary_key=True)

    __table_args__ = (
        # The composite primary key already indexes parent_id; this covers the
        # reverse question, "who contains this group?".
        Index('ix_group_edges_child_id', 'child_id'),
    )


class GroupClosure(Base):
    """Transitive closure of group_edges. One row per reachable pair.

    `depth` is the SHORTEST path length. In a DAG the same descendant is
    frequently reachable by more than one route — a diamond reaches D through
    both B and C — and that must still produce exactly one row, or every
    scoped query silently multiplies its result set.

    Every group carries a depth-0 row pointing at itself, so desc(G) includes
    G and callers never need to special-case "and also my own group".

    Derived state: rebuilt wholesale by H1-2 on any edge change. Never write
    to it directly.
    """
    __tablename__ = 'group_closure'

    ancestor_id = Column(Integer, ForeignKey('groups.id', ondelete='CASCADE'), primary_key=True)
    descendant_id = Column(Integer, ForeignKey('groups.id', ondelete='CASCADE'), primary_key=True)
    depth = Column(Integer, nullable=False)

    __table_args__ = (
        # The composite primary key leads with ancestor_id, which serves
        # desc(G) — the hot path. This covers the inverse lookup.
        Index('ix_group_closure_descendant_id', 'descendant_id'),
    )


class GroupMembership(Base):
    """A user is IN a group.

    Distinct from Grant, which is authority OVER one. A private is a member of
    their company and holds no grant; a battalion commander holds a grant over
    the battalion and is a member of its headquarters. Membership says where
    someone and their equipment sit; a grant says what they may do.
    """
    __tablename__ = 'group_memberships'

    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    group_id = Column(Integer, ForeignKey('groups.id', ondelete='CASCADE'), primary_key=True)

    group = relationship("Group")

    __table_args__ = (
        Index('ix_group_memberships_group_id', 'group_id'),
    )


class Grant(Base):
    """`user` holds `capability` over every member of desc(`group`).

    Authority is positional: the same VIEW grant means "the whole force" on the
    root group and "one company" on a leaf. That is why the three profile
    visibility booleans reduce to grant placement rather than to three columns.
    """
    __tablename__ = 'grants'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    group_id = Column(Integer, ForeignKey('groups.id', ondelete='CASCADE'), nullable=False, index=True)
    capability = Column(String, nullable=False)

    __table_args__ = (
        # Column order is deliberate. The triple is the same set whichever way
        # it is written, so uniqueness is identical -- but the index it builds
        # is not. (user_id, capability, group_id) seeks directly on the H1-3
        # hot path, WHERE user_id = ? AND capability = ?, whereas putting
        # group_id second would seek on user_id alone and filter the rest.
        # user_id therefore needs no index of its own; a standalone one would
        # be a redundant prefix, pure write amplification on the table H1-2 and
        # H1-3 write most. group_id does get one: it leads nothing.
        UniqueConstraint('user_id', 'capability', 'group_id', name='uq_grants_user_group_capability'),
    )


# --- Closure engine --------------------------------------------------------
#
# None of these commit. The caller owns the transaction, matching
# dependencies.py, so an edge change and the closure rebuild it triggers land
# atomically in whatever unit of work the caller is already running. Note that
# get_db() does not commit on exit either: an uncommitted rebuild is discarded
# without complaint.


def detect_cycle(edges: Iterable[tuple[int, int]]) -> None:
    """Raise CycleError if `edges` contains a cycle.

    Containment has to stay acyclic. Antisymmetry is what makes it a partial
    order rather than merely a relation, and a cycle would make desc() unbounded
    as a concept even though the traversal below would still terminate.

    Nodes are added as add(child, parent) -- parent is the predecessor. The
    direction does not affect detection, since a cycle in a graph is a cycle in
    its reverse. What it does affect is the report: graphlib documents each node
    in CycleError.args[1] as the immediate predecessor of the next, so this
    ordering makes the path read as a containment chain, [2, 3, 1, 2] meaning
    "2 contains 3 contains 1 contains 2". Reversing the arguments prints it
    backwards, which is the only reason to care.

    A self-edge is a cycle of length one and needs no separate branch.
    prepare() performs the whole check; there is no need to drain the sorter.
    """
    sorter = TopologicalSorter()
    for parent_id, child_id in edges:
        sorter.add(child_id, parent_id)
    sorter.prepare()


def _live_edges(db: Session) -> tuple[set[int], list[tuple[int, int]]]:
    """Return the live group ids, and the edges whose endpoints are all live.

    Retained as a second line of defence, not as the primary one. The database
    now cascades a deleted group's edges away on both dialects, so in ordinary
    operation there is nothing here to filter. What this guards is the case
    where that did not happen: PRAGMA foreign_keys is per-connection, so any
    SQLite connection opened without database.py's listener -- a migration, a
    maintenance script, a sqlite3 shell -- can still delete a group and leave
    its edges behind.

    Debris matters because it is not inert. A closure row naming a deleted
    group violates the foreign key on Postgres, and the traversal would run
    *through* the dead group and assert that its parent contains its children,
    an over-grant routed via a group that no longer exists. SQLite compounds
    that by reusing the rowid of the highest deleted row, so an orphaned edge
    is adopted by the next group created rather than merely ignored.

    Filtering in Python rather than SQL is deliberate: both tables are read
    whole for the rebuild anyway.
    """
    live = {group_id for (group_id,) in db.query(Group.id).all()}
    edges = [
        (edge.parent_id, edge.child_id)
        for edge in db.query(GroupEdge).all()
        if edge.parent_id in live and edge.child_id in live
    ]
    return live, edges


def rebuild_closure(db: Session) -> None:
    """Recompute group_closure from group_edges. Idempotent.

    Termination does not depend on the edge table being acyclic -- the visited
    check bounds the traversal -- so a corrupted edge set degrades to a closure
    that is wrong but finite, never to a hang.
    """
    # Both SessionLocal (database.py:20) and the test sessionmaker
    # (conftest.py:41) set autoflush=False, so an edge the caller has add()ed
    # but not flushed is invisible to the queries below. Without this flush the
    # rebuild would compute the closure of an edge set missing that edge and
    # report success -- and add_all-then-commit is precisely how seed_data.py
    # writes. The cost is that an unrelated pending object in the caller's unit
    # of work can raise IntegrityError here rather than where it was created.
    # That is the better failure.
    db.flush()

    live, edges = _live_edges(db)
    children = defaultdict(list)
    for parent_id, child_id in edges:
        children[parent_id].append(child_id)

    # No synchronize_session argument on purpose. The default expunges the
    # deleted rows from the session, which is required because the add_all
    # below recreates the same primary keys. Passing False leaves them
    # persistent, the new instances collide with them, and SQLAlchemy warns and
    # picks a winner -- on the table that decides who can see what.
    db.query(GroupClosure).delete()

    rows = []
    for root in live:
        # Breadth-first, so the first time a node is reached is by a shortest
        # path and `depth` means what it claims. popleft(), not pop(): a stack
        # would silently record *a* path length instead of the minimum.
        depth_of = {root: 0}
        queue = deque([root])
        while queue:
            node = queue.popleft()
            for child_id in children.get(node, ()):
                if child_id not in depth_of:
                    depth_of[child_id] = depth_of[node] + 1
                    queue.append(child_id)
        rows.extend(
            GroupClosure(ancestor_id=root, descendant_id=descendant, depth=depth)
            for descendant, depth in depth_of.items()
        )

    db.add_all(rows)
    db.flush()


def add_edge(db: Session, parent_id: int, child_id: int) -> None:
    """Record that `parent` directly contains `child`, then rebuild the closure.

    Everything is validated before anything is written: a rejected edge must not
    leave a live object in db.new for the caller's next flush to commit.

    Re-adding an edge that already exists is a no-op rather than an error. The
    caller owns the transaction, so letting the composite primary key raise
    IntegrityError would poison their entire unit of work over a harmless
    re-assertion. The rebuild still runs, which keeps the postcondition
    unconditional -- when this returns, the closure agrees with the edges -- and
    makes re-adding a known edge a way to repair a damaged closure.
    """
    db.flush()
    live, edges = _live_edges(db)

    # dict.fromkeys rather than a list: a self-edge onto a missing group would
    # otherwise name that group twice. Not a set -- order stays parent, child.
    missing = dict.fromkeys(
        group_id for group_id in (parent_id, child_id) if group_id not in live
    )
    if missing:
        raise ValueError(
            f"Refusing to add edge {parent_id} -> {child_id}: no group exists with id "
            f"{', '.join(str(group_id) for group_id in missing)}. Create the group first."
        )

    if (parent_id, child_id) in edges:
        rebuild_closure(db)
        return

    try:
        detect_cycle([*edges, (parent_id, child_id)])
    except CycleError as exc:
        path = " -> ".join(str(node) for node in exc.args[1])
        raise CycleError(
            f"Refusing to add edge {parent_id} -> {child_id}: containment would become "
            f"cyclic ({path}). Remove an edge on that path first.",
            exc.args[1],
        ) from exc

    db.add(GroupEdge(parent_id=parent_id, child_id=child_id))
    rebuild_closure(db)


def remove_edge(db: Session, parent_id: int, child_id: int) -> None:
    """Drop a direct containment edge, then rebuild the closure.

    Deliberately asymmetric with add_edge: no group-existence check, and a
    missing edge is a silent no-op. Adding is precondition-checked
    construction; removing is demolition, and demolition has to work on rubble.
    Clearing the orphan edges left by a deleted group is exactly the case where
    the endpoints are already gone.
    """
    # Without this the delete matches nothing while the edge is still pending,
    # and rebuild_closure's own flush then writes it -- remove_edge would report
    # success and leave the edge in place. See rebuild_closure.
    db.flush()
    db.query(GroupEdge).filter_by(parent_id=parent_id, child_id=child_id).delete()
    rebuild_closure(db)


# --- Query surface ---------------------------------------------------------
#
# descendant_ids and extent return a Select and take no Session, deliberately
# unlike the engine above: the engine writes and therefore needs a unit of work,
# whereas these only describe a set. A Select composes into the caller's
# statement, so H1-5 can express scoping as one query instead of materialising
# ids and paying a second round trip per request to ship them back as an
# IN (...) literal. primary_group_id is the exception and says why.
#
# Nothing here reads Profile, User.role or any path string: visibility is not a
# special case, it is may(P, VIEW, R).


def descendant_ids(group_ids) -> Select:
    """desc(G) for every G in `group_ids` -- the groups they contain, themselves included.

    `group_ids` is anything IN accepts: a list of ids, or another Select. The
    second form is what lets extent() below be a composition of this function
    rather than a second hand-written join, so "descendants of" keeps exactly
    one definition.

    Self-inclusion needs no OR arm here. It is carried by the depth-0 rows
    rebuild_closure emits for every group, which is the whole reason those rows
    exist -- see GroupClosure.

    distinct() is not decoration, and it belongs here rather than on extent().
    desc(G) is a SET. Ancestors overlap constantly -- a battalion and one of its
    own companies -- and each route to a shared descendant contributes its own
    closure row, so without this descendant_ids([Bn, Co]) returns Co's subtree
    twice. Harmless while the only consumer wraps it in IN (subquery), and not
    harmless at all for the first caller that joins against it: that is the
    silent result-set multiplication the closure table is shaped to avoid. The
    cost is a hash over tens of rows.
    """
    return select(GroupClosure.descendant_id).where(
        GroupClosure.ancestor_id.in_(group_ids)
    ).distinct()


def extent(user_id: int, capability: Capability) -> Select:
    """Every group over which `user` may exercise `capability`.

    extent(P,C) = union of desc(g.group) for each grant g of P carrying C.

    Authority is positional, so this is the whole of the access question: the
    same VIEW grant means "the entire force" on the root group and "one company"
    on a leaf. A resource is visible when its group appears here.

    Overlapping grants -- VIEW on both a battalion and one of its own companies
    -- reach the same subtree by two routes. descendant_ids() dedupes, so this
    needs no distinct() of its own; see the note there.

    A user with no matching grant yields the empty set, not everything. There is
    no MASTER arm: H1-10 makes MASTER a grant on the root group rather than a
    role comparison.
    """
    return descendant_ids(
        select(Grant.group_id).where(
            Grant.user_id == user_id,
            # .value, not the bare member. For a real Capability the two are
            # equivalent -- it subclasses str -- but reading .value is what
            # makes a caller who passes the bare string "VIEW" fail with an
            # AttributeError rather than silently working, which keeps the type
            # hint above enforced instead of merely advisory. Pinned by
            # test_a_plain_string_capability_is_refused_loudly.
            Grant.capability == capability.value,
        )
    )


def primary_group_id(db: Session, user_id: int) -> int | None:
    """The group that equipment held by `user` belongs to.

    One definition, shared by every equipment write path, so that a created
    item, an assigned item and a transferred item all land in the same place
    for the same person.

    Unlike the two functions above this takes a Session and executes. Those
    describe a set for the caller to compose into a query; this must produce a
    scalar to be written into a column, and a Select cannot be assigned to one.

    Membership is many-to-many, so "the user's group" is a rule rather than a
    lookup. The rule is MOST SPECIFIC: a user who is a member of both a
    battalion and one of its companies puts equipment in the company. Anything
    else would place items above the commander who actually holds them and
    widen who can see them.

    Two memberships can also be incomparable -- a company and a task force that
    contains neither the other -- and there the answer is a genuine choice. min()
    makes it deterministic rather than dependent on row order, which matters
    because the alternative is an item that changes unit between two identical
    requests. The set is never empty: containment is acyclic, so at least one
    membership contains no other.

    Returns None when the user is a member of nothing. Callers decide what that
    means; equipment.py leaves the existing group in place rather than nulling
    it, since a NULL group is visible to no commander at all.

    Sharp edge, inherited: a group with no closure rows -- one created without
    rebuild_closure, see GroupClosure -- appears in no `containers` set and can
    therefore be selected here, yielding an item nobody can see. The fix belongs
    wherever groups are created, not here.
    """
    member_ids = {
        group_id
        for (group_id,) in db.query(GroupMembership.group_id).filter_by(user_id=user_id)
    }
    if not member_ids:
        return None

    # A membership that strictly contains another membership is not the most
    # specific one. depth > 0 excludes the self-row every group carries, which
    # would otherwise make every membership its own container and empty the set.
    containers = {
        ancestor_id
        for (ancestor_id,) in db.query(GroupClosure.ancestor_id).filter(
            GroupClosure.ancestor_id.in_(member_ids),
            GroupClosure.descendant_id.in_(member_ids),
            GroupClosure.depth > 0,
        )
    }
    return min(member_ids - containers)


# --- The gate --------------------------------------------------------------
#
# Everything above describes or computes. This decides, and raises. Keeping the
# two apart is what lets a caller ask the question without catching an
# exception to hear the answer.


def may(db: Session, user_id: int, capability: Capability, group_id: int | None) -> bool:
    """may(P, C, R) -- may `user` exercise `capability` over a resource in `group`?

    The whole access question, and the only one the system ever asks. A
    resource's sole relevant property is which group it is in, which is why
    this takes the group rather than the resource: authz.py is built not to
    know what an Equipment is, and callers write

        authz.may(db, user.id, Capability.TRANSFER, item.group_id)

    so the mapping from resource to group stays visible at the call site
    instead of hiding inside an isinstance ladder here.

    Composed from extent() rather than written beside it. `? IN (SELECT ...)`
    reuses the closure join verbatim; a second hand-written join would be a
    small DATA-H9 inside the module that exists to prevent one. It also
    inherits extent()'s .value read, so a bare string capability raises
    AttributeError rather than quietly answering False.

    A group of None is refused, and the guard is not defensive padding. It
    used to be justified by Equipment.group_id being nullable; H1-11 made that
    column NOT NULL and the guard stayed load-bearing, because the None never
    came from the resource in the first place. It comes from the CALLER's
    side: primary_group_id() answers None for a user who is a member of
    nothing, and equipment.py's _creation_group_id passes that straight into
    require() -- which is precisely how H1-6 makes creation fail closed for a
    user with nowhere to put the item.

    Without the branch, `NULL IN (...)` is NULL in SQL -- neither true nor
    false -- and the surrounding code would read the missing row as a denial
    by accident rather than by decision, on semantics that would have to hold
    identically on both dialects. Nothing reaches no group, so: deny, and say
    so in Python.

    No MASTER arm, matching extent(), and since H1-10 there is no master arm
    anywhere else either -- is_master is deleted and the last short-circuit,
    in dependencies.scope_equipment_query, became a VIEW grant on the root.
    An ungranted master is therefore denied here and everywhere, which is a
    real consequence and is pinned by tests rather than assumed.
    """
    if group_id is None:
        return False

    return db.execute(
        select(literal(1)).where(literal(group_id).in_(extent(user_id, capability)))
    ).first() is not None


EQUIPMENT_CAPABILITIES = (
    Capability.TRANSFER,
    Capability.CREATE_EQUIPMENT,
    Capability.REPORT_STATUS,
    Capability.RESOLVE_FAULT,
)


def implied_view_placements(placements):
    """The VIEW grants a table of other grants requires, by the rule:

        a verb over equipment in a group implies VIEW of that group

    You see what you may act on. H1-9 and H1-10 each hand-placed a row to
    satisfy this and each labelled it a judgement asking to be revisited --
    RESOLVE_FAULT's split, and the brigade tech's root VIEW. Both were reaching
    for this rule, so H1-10.5 states it once and derives the rows.

    It lives here rather than in either caller because BOTH the seed and the
    test fixture need it, and a rule implemented twice is the DATA-H9 shape the
    model exists to avoid. Note what is NOT shared: the grant tables themselves
    stay declared separately, because a test that asserts the seed's shape by
    reading the seed asserts nothing. Sharing the rule is not sharing the data.

    The GLOBAL capabilities are excluded, and that exclusion is the whole
    reason this takes a filtered tuple rather than every key it is handed.
    MANAGE_CATALOG and MANAGE_PERSONNEL sit on the root and authorise
    vocabulary and people; folding them in would hand anyone who may create a
    user sight of every item in the force. In both seeded tables their holders
    happen to hold root VIEW anyway, so the exclusion is invisible in the data
    and has to be asserted against this function directly.

    Takes {capability: iterable of (holder, node)} and returns the set of
    (holder, node) pairs that must carry VIEW. Holder is whatever the caller
    uses to identify a user -- an ORM object in the seed, a string key in the
    fixture -- because this function never dereferences it.
    """
    return {
        pair
        for capability in EQUIPMENT_CAPABILITIES
        for pair in placements.get(capability, ())
    }


def root_groups(db: Session) -> list[Group]:
    """The graph's tops: groups nothing contains.

    "Root" was defined in bootstrap_admin first, because that script had to
    place a brand-new MASTER somewhere and a root is the only definition of
    "the top" available to code that cannot know the organisation it is
    bootstrapping. It is defined here now because a second caller appeared --
    require_global below -- and two copies of this predicate is exactly the
    DATA-H9 shape the model exists to avoid.

    A list rather than one row. Nothing forbids several disconnected trees, and
    a rule that silently picked the first would depend on row order.
    """
    return db.query(Group).filter(
        ~Group.id.in_(db.query(GroupEdge.child_id))
    ).all()


def may_global(db: Session, user_id: int, capability: Capability) -> bool:
    """may(P, C, R) for a resource that belongs to the whole force.

    Some resources have no group and never will: CatalogItem and FaultType are
    global vocabulary, one shared namespace for every unit. Through H1-9 that
    was read as putting them outside the algebra entirely. It does not -- a
    resource belonging to everyone is scoped by the node that MEANS everyone,
    and the graph has one. The verb is asked over the root.

    EVERY root, not any. On the seeded graph, and on anything bootstrap_admin
    creates, there is exactly one and the two readings coincide -- which is
    precisely why the rule has to be chosen now rather than discovered later by
    whoever first adds a second tree. Authority over a shared namespace is
    authority over all of it; holding the verb over one tree while another tree
    exists is not authority over the vocabulary both of them use.

    Fails closed on an empty graph. No roots means no node stands for the whole
    force, so nobody holds authority over it, and all() would say the opposite.
    """
    roots = root_groups(db)
    if not roots:
        return False
    return all(may(db, user_id, capability, group.id) for group in roots)


def require_global(db: Session, user_id: int, capability: Capability) -> None:
    """may_global(), as a gate. Returns nothing, or raises 403.

    403 rather than 404, with no resolver in front, and that is not a breach of
    the ordering rule require() documents. That rule exists to stop a status
    code confirming the existence of a row the caller cannot see. Here there is
    no such row: the routes this guards address global vocabulary or the
    personnel table, and the caller learns nothing about any resource from
    being refused a verb they do not hold.
    """
    if not may_global(db, user_id, capability):
        raise HTTPException(
            status_code=403,
            detail=f"Permission denied. You do not hold {capability.value} over this system.",
        )


def require(db: Session, user_id: int, capability: Capability, group_id: int | None) -> None:
    """may(), as a gate. Returns nothing, or raises 403.

    Two lines on purpose: one definition of the decision, one place that turns
    it into a status code.

    403 is the right code here only because callers reach this AFTER resolving
    the resource within their own VIEW extent -- see
    dependencies.get_scoped_equipment_or_404, which returns 404 for anything
    the caller cannot see. That ordering is what makes the pair safe:

        resolve within VIEW  ->  404 if they cannot see it   (no enumeration)
        require(capability)  ->  403 if they see it but may not act

    A 403 therefore only ever reaches someone who could already list the
    resource, and discloses nothing they did not have. Calling require() on a
    raw id straight from the request body inverts that and turns this into the
    enumeration oracle the resolver exists to avoid.

    The detail names the verb and never the group. The caller already knows
    what they attempted; they must not learn anything about the target.
    """
    if not may(db, user_id, capability, group_id):
        raise HTTPException(
            status_code=403,
            detail=f"Permission denied. You do not hold {capability.value} over this resource.",
        )
