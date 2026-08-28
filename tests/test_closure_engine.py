"""
Behavioural coverage for H1-2 (TODO-SEC-H1.md) -- the closure engine.

group_closure is derived state. Nothing reads it yet (the query surface is
H1-3), so every defect it can carry is silent today and becomes a wrong answer
to "who may see this?" the moment H1-5 joins against it. Two failure modes are
worth naming, because they are the ones that do not announce themselves:

  * A duplicated (ancestor, descendant) pair multiplies the result set of every
    scoped query. A DAG diamond reaches the same descendant twice, so this is
    the default outcome, not an exotic one -- hence closure() below refuses to
    collapse duplicates silently.
  * A closure row that outlives the edge or the group that justified it is a
    standing over-grant. Removal and deletion therefore get as much coverage
    here as construction.

The engine is a full recompute, so the tests treat it as one: they assert the
whole relation, not the rows a particular call touched. The reachability test
checks it against a second implementation using a different algorithm, which is
the only test here that would survive a rewrite of rebuild_closure.
"""
import math
import warnings
from itertools import pairwise

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import sessionmaker

from backend import authz, models
from backend.authz import CycleError
from backend.database import Base

# --- helpers --------------------------------------------------------------

def make_groups(db, *names):
    """Create Units by name and flush so they have ids. Deliberately no commit."""
    groups = {name: authz.Unit(name=name) for name in names}
    db.add_all(groups.values())
    db.flush()
    return groups


def closure(db, groups):
    """The closure as {(ancestor_name, descendant_name): depth}.

    Asserts one row per pair rather than letting the dict swallow duplicates:
    a doubled row is the diamond defect this engine exists to avoid, and it
    would be invisible in every assertion below if this helper deduplicated.
    """
    names = {group.id: name for name, group in groups.items()}
    rows = [
        (
            names.get(row.ancestor_id, f"<unknown:{row.ancestor_id}>"),
            names.get(row.descendant_id, f"<unknown:{row.descendant_id}>"),
            row.depth,
        )
        for row in db.query(authz.GroupClosure).all()
    ]
    pairs = {(ancestor, descendant): depth for ancestor, descendant, depth in rows}
    assert len(pairs) == len(rows), f"duplicate closure rows: {sorted(rows)}"
    return pairs


def edge_set(db, groups):
    """Every group_edges row as (parent_name, child_name), debris included."""
    names = {group.id: name for name, group in groups.items()}
    return {
        (
            names.get(edge.parent_id, f"<unknown:{edge.parent_id}>"),
            names.get(edge.child_id, f"<unknown:{edge.child_id}>"),
        )
        for edge in db.query(authz.GroupEdge).all()
    }


def link(db, groups, *pairs):
    for parent, child in pairs:
        authz.add_edge(db, groups[parent].id, groups[child].id)


# --- reflexivity, transitivity, depth -------------------------------------

def test_every_group_contains_itself(db_session):
    """desc(G) includes G, so callers never special-case "and also my own group"."""
    groups = make_groups(db_session, "Bde188", "Bn53", "Detached")
    link(db_session, groups, ("Bde188", "Bn53"))

    assert closure(db_session, groups) == {
        ("Bde188", "Bde188"): 0,
        ("Bn53", "Bn53"): 0,
        ("Detached", "Detached"): 0,
        ("Bde188", "Bn53"): 1,
    }


def test_containment_is_transitive(db_session):
    """A brigade contains its companies without an edge saying so."""
    groups = make_groups(db_session, "Bde188", "Bn53", "CoA")
    link(db_session, groups, ("Bde188", "Bn53"), ("Bn53", "CoA"))

    pairs = closure(db_session, groups)
    assert pairs[("Bde188", "CoA")] == 2
    # ...and containment is one-directional: a company does not contain its brigade.
    assert ("CoA", "Bde188") not in pairs


def test_depth_is_the_shortest_path_not_the_discovered_one(db_session):
    """A shortcut edge must lower the recorded depth.

    Bde188 reaches CoA both directly and through Bn53. depth is documented as
    the shortest path, so the direct route wins -- a depth-first traversal, or
    a queue popped from the wrong end, would record 2 here and still look
    plausible.
    """
    groups = make_groups(db_session, "Bde188", "Bn53", "CoA")
    link(db_session, groups, ("Bde188", "Bn53"), ("Bn53", "CoA"), ("Bde188", "CoA"))

    assert closure(db_session, groups)[("Bde188", "CoA")] == 1


def test_adding_a_shortcut_lowers_an_existing_depth(db_session):
    """The rebuild re-derives depth; it does not merely fill in missing pairs."""
    groups = make_groups(db_session, "Bde188", "Bn53", "CoA")
    link(db_session, groups, ("Bde188", "Bn53"), ("Bn53", "CoA"))
    assert closure(db_session, groups)[("Bde188", "CoA")] == 2

    link(db_session, groups, ("Bde188", "CoA"))
    assert closure(db_session, groups)[("Bde188", "CoA")] == 1


def test_a_long_chain_has_no_missing_or_extra_rows(db_session):
    """A 12-link chain: every prefix pair present exactly once, depth = distance."""
    names = [f"L{i}" for i in range(12)]
    groups = make_groups(db_session, *names)
    link(db_session, groups, *pairwise(names))

    assert closure(db_session, groups) == {
        (names[i], names[j]): j - i for i in range(12) for j in range(i, 12)
    }


# --- the DAG cases --------------------------------------------------------

def test_a_diamond_reaches_its_sink_through_exactly_one_row(db_session):
    """The defect the closure table is shaped to avoid.

    Bde188 reaches CoA through both battalions. Two rows would silently double
    every scoped query's result set once H1-5 joins against this table.
    """
    groups = make_groups(db_session, "Bde188", "Bn53", "Bn71", "CoA")
    link(db_session, groups, ("Bde188", "Bn53"), ("Bde188", "Bn71"), ("Bn53", "CoA"), ("Bn71", "CoA"))

    rows = [
        row for row in db_session.query(authz.GroupClosure).all()
        if (row.ancestor_id, row.descendant_id) == (groups["Bde188"].id, groups["CoA"].id)
    ]
    assert len(rows) == 1
    assert rows[0].depth == 2


def test_a_group_may_have_several_parents(db_session):
    """Why containment is a DAG: a seconded company keeps its battalion.

    Each parent contains CoA in its own right, and neither gains the other's
    subtree by sharing a child.
    """
    groups = make_groups(db_session, "Bn53", "TFSinai", "CoA", "CoB")
    link(db_session, groups, ("Bn53", "CoA"), ("Bn53", "CoB"), ("TFSinai", "CoA"))

    pairs = closure(db_session, groups)
    assert pairs[("Bn53", "CoA")] == 1
    assert pairs[("TFSinai", "CoA")] == 1
    assert ("TFSinai", "CoB") not in pairs, "a shared child must not merge the two parents"
    assert ("Bn53", "TFSinai") not in pairs


def test_disconnected_components_do_not_leak_into_each_other(db_session):
    groups = make_groups(db_session, "Bn53", "CoA", "TFSinai", "CoZ")
    link(db_session, groups, ("Bn53", "CoA"), ("TFSinai", "CoZ"))

    pairs = closure(db_session, groups)
    assert ("Bn53", "CoZ") not in pairs
    assert ("TFSinai", "CoA") not in pairs
    assert len(pairs) == 6  # four self-rows plus the two edges


def test_closure_is_exactly_the_reachability_relation(db_session):
    """The engine, checked against a second implementation of the same relation.

    Every other test here asserts a hand-computed answer, so all of them would
    survive a rewrite that broke a case nobody thought to enumerate. This one
    compares the engine's breadth-first recompute against repeated relaxation
    to a fixpoint -- a different algorithm -- over a graph carrying a diamond,
    a shortcut, two routes of unequal length to the same sink, a node with
    parents in two subtrees, and an unconnected pair.
    """
    names = [f"G{i}" for i in range(10)]
    graph = [
        ("G0", "G1"), ("G0", "G2"), ("G1", "G3"), ("G2", "G3"),   # diamond
        ("G3", "G4"), ("G0", "G4"),                               # shortcut past the diamond
        ("G2", "G5"), ("G5", "G6"), ("G6", "G7"), ("G1", "G7"),   # long route and short route to G7
        ("G8", "G9"), ("G4", "G9"),                               # G9 reached from both subtrees
    ]
    groups = make_groups(db_session, *names)
    link(db_session, groups, *graph)

    # Bellman-Ford-style relaxation: relax every edge until nothing improves.
    reference = {(name, name): 0 for name in names}
    improved = True
    while improved:
        improved = False
        for parent, child in graph:
            for (ancestor, descendant), depth in list(reference.items()):
                if descendant == parent and reference.get((ancestor, child), math.inf) > depth + 1:
                    reference[(ancestor, child)] = depth + 1
                    improved = True

    assert closure(db_session, groups) == reference


# --- rejection: cycles and unknown groups ---------------------------------

def test_a_cycle_is_rejected_and_changes_nothing(db_session):
    """Antisymmetry is what makes containment a partial order.

    The rejected edge must leave no trace: not in group_edges, not pending in
    the session for the caller's next flush to commit, and not in the closure.
    """
    groups = make_groups(db_session, "Bde188", "Bn53", "CoA")
    link(db_session, groups, ("Bde188", "Bn53"), ("Bn53", "CoA"))
    before = closure(db_session, groups)

    with pytest.raises(CycleError):
        authz.add_edge(db_session, groups["CoA"].id, groups["Bde188"].id)

    assert not db_session.new, "a rejected edge was left pending in the session"
    db_session.commit()
    assert edge_set(db_session, groups) == {("Bde188", "Bn53"), ("Bn53", "CoA")}
    assert closure(db_session, groups) == before


def test_a_self_edge_is_rejected(db_session):
    """A cycle of length one. No separate branch, so it needs its own test."""
    groups = make_groups(db_session, "Bn53")
    authz.rebuild_closure(db_session)

    with pytest.raises(CycleError):
        authz.add_edge(db_session, groups["Bn53"].id, groups["Bn53"].id)

    assert not db_session.new
    assert edge_set(db_session, groups) == set()
    assert closure(db_session, groups) == {("Bn53", "Bn53"): 0}


def test_the_cycle_error_names_the_containment_path(db_session):
    """The message has to say which existing edges to remove, not just "no"."""
    groups = make_groups(db_session, "Bde188", "Bn53", "CoA")
    link(db_session, groups, ("Bde188", "Bn53"), ("Bn53", "CoA"))

    with pytest.raises(CycleError) as excinfo:
        authz.add_edge(db_session, groups["CoA"].id, groups["Bde188"].id)

    message = str(excinfo.value.args[0])
    assert "cyclic" in message
    # Every group on the offending path is named, so the operator can pick an
    # edge to cut without reading the closure table.
    for group in groups.values():
        assert str(group.id) in message


def test_cycle_error_is_a_value_error():
    """graphlib's CycleError subclasses ValueError, and callers will rely on it.

    Both rejection paths in add_edge therefore land in a caller's `except
    ValueError`. If CycleError were ever redefined locally as a bare Exception,
    every such handler would start leaking a 500.
    """
    assert issubclass(CycleError, ValueError)


@pytest.mark.parametrize("missing", ["parent", "child", "both"])
def test_add_edge_refuses_to_reference_a_group_that_does_not_exist(db_session, missing):
    """Otherwise the edge is debris the closure will silently ignore forever."""
    groups = make_groups(db_session, "Bn53")
    real, absent = groups["Bn53"].id, 9999
    parent = absent if missing in ("parent", "both") else real
    child = absent if missing in ("child", "both") else real

    with pytest.raises(ValueError, match="no group exists") as excinfo:
        authz.add_edge(db_session, parent, child)

    assert not isinstance(excinfo.value, CycleError), "existence is checked before acyclicity"
    message = str(excinfo.value)
    assert f"Refusing to add edge {parent} -> {child}" in message
    # The list of missing groups names each one once. A self-edge onto a
    # missing group has the id in both positions, and listing it twice there
    # reads as two distinct groups. (The prefix above echoes the requested
    # edge, so the id legitimately appears more than once in the whole string.)
    assert message.split("no group exists with id ")[1].split(".")[0] == str(absent)
    assert not db_session.new
    assert edge_set(db_session, groups) == set()


# --- removal --------------------------------------------------------------

def test_removing_one_route_through_a_diamond_keeps_the_other(db_session):
    """The pair survives at the same depth; only the removed route is gone."""
    groups = make_groups(db_session, "Bde188", "Bn53", "Bn71", "CoA")
    link(db_session, groups, ("Bde188", "Bn53"), ("Bde188", "Bn71"), ("Bn53", "CoA"), ("Bn71", "CoA"))

    authz.remove_edge(db_session, groups["Bn53"].id, groups["CoA"].id)

    pairs = closure(db_session, groups)
    assert pairs[("Bde188", "CoA")] == 2
    assert pairs[("Bn71", "CoA")] == 1
    assert ("Bn53", "CoA") not in pairs

    authz.remove_edge(db_session, groups["Bn71"].id, groups["CoA"].id)

    pairs = closure(db_session, groups)
    assert ("Bde188", "CoA") not in pairs, "a closure row outlived the last edge justifying it"
    assert pairs[("CoA", "CoA")] == 0


def test_removing_every_edge_leaves_only_self_rows(db_session):
    groups = make_groups(db_session, "Bde188", "Bn53", "CoA")
    link(db_session, groups, ("Bde188", "Bn53"), ("Bn53", "CoA"))

    authz.remove_edge(db_session, groups["Bde188"].id, groups["Bn53"].id)
    authz.remove_edge(db_session, groups["Bn53"].id, groups["CoA"].id)

    assert closure(db_session, groups) == {(name, name): 0 for name in groups}


def test_removing_an_edge_that_was_never_there_is_a_no_op(db_session):
    """Demolition has to work on rubble -- but it must still leave the closure sound."""
    groups = make_groups(db_session, "Bde188", "Bn53", "CoA")
    link(db_session, groups, ("Bde188", "Bn53"))
    before = closure(db_session, groups)

    authz.remove_edge(db_session, groups["Bn53"].id, groups["CoA"].id)
    authz.remove_edge(db_session, 9999, 8888)

    assert closure(db_session, groups) == before


# --- the traps: unflushed writes, deleted groups, corrupted state ---------

def test_the_rebuild_sees_edges_the_caller_has_not_flushed(db_session):
    """Both sessionmakers set autoflush=False, and seed_data.py writes add_all-then-commit.

    Without the engine's own flush, the rebuild would read an edge set missing
    the pending row and report success -- the closure would be quietly wrong
    for exactly the code path that populates the graph.
    """
    groups = make_groups(db_session, "Bde188", "Bn53")
    db_session.add(authz.GroupEdge(parent_id=groups["Bde188"].id, child_id=groups["Bn53"].id))

    authz.rebuild_closure(db_session)

    assert closure(db_session, groups)[("Bde188", "Bn53")] == 1


def test_remove_edge_deletes_an_edge_that_is_still_pending(db_session):
    """A bulk DELETE matches nothing while the row is only in db.new.

    The engine's flush is what stops remove_edge from reporting success and
    leaving the edge in place -- the rebuild's own flush would then write it.
    """
    groups = make_groups(db_session, "Bde188", "Bn53")
    db_session.add(authz.GroupEdge(parent_id=groups["Bde188"].id, child_id=groups["Bn53"].id))

    authz.remove_edge(db_session, groups["Bde188"].id, groups["Bn53"].id)

    assert edge_set(db_session, groups) == set()
    assert closure(db_session, groups) == {("Bde188", "Bde188"): 0, ("Bn53", "Bn53"): 0}


def test_deleting_a_group_takes_its_edges_with_it(db_session):
    """The declared ondelete=CASCADE, actually firing.

    It only fires because backend.database enables PRAGMA foreign_keys on every
    SQLite connection; SQLite parses REFERENCES and then ignores it otherwise.
    Bde188 must not be left containing CoA by way of a group that no longer
    exists, and the debris that would say so must not survive the delete.
    """
    groups = make_groups(db_session, "Bde188", "Bn53", "CoA")
    link(db_session, groups, ("Bde188", "Bn53"), ("Bn53", "CoA"))

    db_session.delete(groups["Bn53"])
    authz.rebuild_closure(db_session)

    assert edge_set(db_session, groups) == set(), "the cascade left orphan edges behind"
    assert closure(db_session, groups) == {("Bde188", "Bde188"): 0, ("CoA", "CoA"): 0}


def test_a_reused_rowid_inherits_no_containment(db_session):
    """The defect PRAGMA foreign_keys=ON was turned on to close.

    SQLite hands out the rowid of a deleted row again when it was the highest,
    so an orphaned edge does not merely sit there being ignored -- the next
    group created adopts it. Bde188 would silently contain a group nobody ever
    linked to it. The cascade is what stops the edge existing to be adopted.

    SecretBn must hold the highest id for the reuse to happen, which is why it
    is created last.
    """
    groups = make_groups(db_session, "Bde188", "SecretBn")
    link(db_session, groups, ("Bde188", "SecretBn"))
    doomed_id = groups["SecretBn"].id

    db_session.delete(groups["SecretBn"])
    db_session.flush()

    newcomer = authz.Unit(name="UnrelatedTaskForce")
    db_session.add(newcomer)
    db_session.flush()
    assert newcomer.id == doomed_id, "no rowid reuse happened; the test proves nothing"

    authz.rebuild_closure(db_session)

    groups["UnrelatedTaskForce"] = newcomer
    del groups["SecretBn"]
    assert closure(db_session, groups) == {
        ("Bde188", "Bde188"): 0,
        ("UnrelatedTaskForce", "UnrelatedTaskForce"): 0,
    }


def test_debris_from_a_non_enforcing_connection_is_still_refused(tmp_path):
    """_live_edges' filter, which the cascade otherwise hides completely.

    PRAGMA foreign_keys is per-connection, and backend.migrations deliberately
    opens connections without it, so a group deleted without its edges
    cascading away is not hypothetical. Walking that debris would have Bde188
    contain CoA by way of a group that no longer exists.

    Built on its own engine with no listener attached, because on the suite's
    engine the cascade makes this state unreachable -- which is precisely why
    the guard needs its own test rather than riding on the deletion tests.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'nofk.db'}")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        groups = make_groups(db, "Bde188", "Bn53", "CoA")
        link(db, groups, ("Bde188", "Bn53"), ("Bn53", "CoA"))

        db.delete(groups["Bn53"])
        db.flush()
        assert edge_set(db, groups) == {("Bde188", "Bn53"), ("Bn53", "CoA")}, (
            "this engine cascaded after all; the test is not exercising the filter"
        )

        authz.rebuild_closure(db)

        assert closure(db, groups) == {("Bde188", "Bde188"): 0, ("CoA", "CoA"): 0}
    finally:
        db.close()
        engine.dispose()


def test_a_cyclic_edge_table_degrades_to_a_wrong_answer_not_a_hang(db_session):
    """Raw SQL and seeds can bypass add_edge, so the traversal must not trust them.

    Termination is bounded by the visited check, never by acyclicity. What
    comes out is still reachability -- it is the *order* that stops meaning
    anything once the graph has a cycle, which is why add_edge refuses to
    create one.
    """
    groups = make_groups(db_session, "A", "B", "C")
    ids = {name: group.id for name, group in groups.items()}
    for parent, child in [("A", "B"), ("B", "C"), ("C", "A")]:
        db_session.execute(
            text("INSERT INTO group_edges (parent_id, child_id) VALUES (:p, :c)"),
            {"p": ids[parent], "c": ids[child]},
        )

    authz.rebuild_closure(db_session)

    pairs = closure(db_session, groups)
    assert len(pairs) == 9, "every group reaches every other in a 3-cycle"
    assert pairs[("A", "A")] == 0 and pairs[("A", "B")] == 1 and pairs[("A", "C")] == 2


def test_readding_a_known_edge_repairs_a_damaged_closure(db_session):
    """The no-op path still rebuilds, which is what makes it a repair tool.

    Raising IntegrityError instead would poison the caller's whole unit of work
    over a harmless re-assertion, since the caller owns the transaction.
    """
    groups = make_groups(db_session, "Bde188", "Bn53", "CoA")
    link(db_session, groups, ("Bde188", "Bn53"), ("Bn53", "CoA"))
    intact = closure(db_session, groups)

    # Damage it both ways: drop a true row and fabricate a false one.
    db_session.query(authz.GroupClosure).filter_by(
        ancestor_id=groups["Bde188"].id, descendant_id=groups["CoA"].id
    ).delete()
    db_session.add(authz.GroupClosure(
        ancestor_id=groups["CoA"].id, descendant_id=groups["Bde188"].id, depth=1
    ))

    authz.add_edge(db_session, groups["Bde188"].id, groups["Bn53"].id)

    assert closure(db_session, groups) == intact
    assert edge_set(db_session, groups) == {("Bde188", "Bn53"), ("Bn53", "CoA")}


# --- invariants of the recompute itself -----------------------------------

def test_the_rebuild_is_idempotent(db_session):
    groups = make_groups(db_session, "Bde188", "Bn53", "Bn71", "CoA")
    link(db_session, groups, ("Bde188", "Bn53"), ("Bde188", "Bn71"), ("Bn53", "CoA"), ("Bn71", "CoA"))
    once = closure(db_session, groups)

    for _ in range(3):
        authz.rebuild_closure(db_session)

    assert closure(db_session, groups) == once


def test_rebuilding_over_rows_already_loaded_does_not_collide(db_session):
    """Why the bulk delete keeps its default synchronize_session.

    The rebuild recreates the primary keys it just deleted. If the deleted rows
    stayed persistent in the session, the new instances would collide with them
    and SQLAlchemy would warn and pick a winner -- on the table that decides
    who can see what. Loading the closure first is what puts them in the
    identity map to collide with.

    `loaded` must stay referenced across the rebuild. The identity map holds
    weak references, so discarding the list lets the rows be collected out of
    it before the collision can happen, and this test then passes against a
    deliberately broken bulk delete. Verified by mutation, not assumed.
    """
    groups = make_groups(db_session, "Bde188", "Bn53")
    link(db_session, groups, ("Bde188", "Bn53"))
    loaded = db_session.query(authz.GroupClosure).all()

    with warnings.catch_warnings():
        warnings.simplefilter("error", SAWarning)
        authz.rebuild_closure(db_session)

    assert len(loaded) == 3, "the rows that had to still be in the identity map"
    assert closure(db_session, groups)[("Bde188", "Bn53")] == 1


def test_rebuilding_an_empty_database_is_not_an_error(db_session):
    authz.rebuild_closure(db_session)
    assert db_session.query(authz.GroupClosure).count() == 0


def test_the_engine_commits_nothing(db_session):
    """The caller owns the transaction, so an edge and its closure land together.

    get_db() does not commit on exit either, so a caller who forgets loses the
    rebuild silently -- which is the documented contract, and the reason this
    is pinned rather than assumed.
    """
    groups = make_groups(db_session, "Bde188", "Bn53")
    db_session.commit()

    authz.add_edge(db_session, groups["Bde188"].id, groups["Bn53"].id)
    db_session.rollback()

    assert db_session.query(authz.GroupEdge).count() == 0
    assert db_session.query(authz.GroupClosure).count() == 0


# --- detect_cycle on its own ----------------------------------------------

@pytest.mark.parametrize(
    "edges",
    [
        pytest.param([], id="empty"),
        pytest.param([(1, 2), (2, 3), (1, 3)], id="shortcut"),
        pytest.param([(1, 3), (2, 3)], id="two-parents"),
        pytest.param([(1, 2), (1, 3), (2, 4), (3, 4)], id="diamond"),
        pytest.param([(1, 2), (3, 4)], id="disconnected"),
    ],
)
def test_detect_cycle_accepts_every_shape_a_dag_can_take(edges):
    authz.detect_cycle(edges)


@pytest.mark.parametrize(
    "edges",
    [
        pytest.param([(1, 1)], id="self-edge"),
        pytest.param([(1, 2), (2, 1)], id="two-cycle"),
        pytest.param([(1, 2), (2, 3), (3, 1)], id="three-cycle"),
        pytest.param([(1, 2), (3, 4), (4, 5), (5, 3)], id="cycle-in-one-component"),
    ],
)
def test_detect_cycle_rejects_every_cycle(edges):
    with pytest.raises(CycleError):
        authz.detect_cycle(edges)


def test_the_reported_path_reads_as_a_containment_chain():
    """graphlib documents each node as the predecessor of the next.

    add() is called as add(child, parent) so the path prints in containment
    order. Reversing the arguments prints it backwards -- detection works
    either way, which is what makes this worth pinning.
    """
    with pytest.raises(CycleError) as excinfo:
        authz.detect_cycle([(1, 2), (2, 3), (3, 1)])

    path = excinfo.value.args[1]
    assert path[0] == path[-1], "graphlib closes the reported cycle"
    for parent, child in pairwise(path):
        assert (parent, child) in {(1, 2), (2, 3), (3, 1)}, f"{parent} does not contain {child}"


# --- membership and grants are untouched by the engine --------------------

def test_the_rebuild_leaves_memberships_and_grants_alone(db_session):
    """Only group_closure is derived.

    Wiping a grant here would be catastrophic and entirely silent until H1-3
    asked who could see anything.
    """
    groups = make_groups(db_session, "Bde188", "Bn53")
    user = models.User(personal_number="u1", full_name="One")
    db_session.add(user)
    db_session.flush()
    db_session.add_all([
        authz.GroupMembership(user_id=user.id, group_id=groups["Bn53"].id),
        authz.Grant(user_id=user.id, group_id=groups["Bde188"].id, capability="VIEW"),
    ])

    authz.add_edge(db_session, groups["Bde188"].id, groups["Bn53"].id)
    authz.remove_edge(db_session, groups["Bde188"].id, groups["Bn53"].id)

    assert db_session.query(authz.GroupMembership).count() == 1
    assert db_session.query(authz.Grant).count() == 1
