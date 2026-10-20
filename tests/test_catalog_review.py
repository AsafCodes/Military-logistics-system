"""SEC-M14: nothing reaches the fault-type list without a second account.

Two defects, one rule. POST /setup/fault_types was open to every authenticated
user, and MANAGE_CATALOG both let its holder's proposal skip review and
approved proposals, so the approval workflow separated nobody from anything.
report_fault had the same skip for any REPORT_STATUS holder over the item's
group, which gave a company commander a say over vocabulary every unit reads.

What holds now, and what this file pins:

  - creating through the setup route needs MANAGE_CATALOG (403 otherwise,
    nothing written; tests/test_global_authority.py has the refusals);
  - every new fault type starts pending, from either route, whoever asks;
  - approve_fault_type refuses the account in requested_by_id, so review
    always takes a second MANAGE_CATALOG holder. A type with no proposer
    (the seeded vocabulary) is approvable as before.

The separation is a check on requested_by_id rather than a second verb, by
decision at SEC-M14's plan: both seeded holders would hold both verbs. The
cost is named in Capability's docstring: a database with one holder, as a
bootstrapped one is, cannot approve that holder's own proposals.

The last two tests read the routers' source rather than drive them, so that a
third route minting fault types, or a second place approving one, fails here
before anyone writes a behavioural test for it.
"""
import ast
import re
from pathlib import Path

import pytest

import backend
from backend import models
from tests.test_global_authority import (
    a_fault,
    approve_fault,
    create_fault,
    pending_faults,
)
from tests.test_status_authority import item_named, report

ROUTERS = Path(backend.__file__).parent / "routers"
HOLDERS = ("u_master", "u_brig_cmdr")


def user_id(db, personal_number):
    return db.query(models.User).filter_by(personal_number=personal_number).one().id


def fault_named(db, name):
    return db.query(models.FaultType).filter_by(name=name).one()


# --- the two-person rule ----------------------------------------------------


@pytest.mark.parametrize("proposer,approver", [HOLDERS, HOLDERS[::-1]])
def test_a_holder_cannot_approve_their_own_proposal_and_the_other_can(
    client, db_session, mock_matrix_db, proposer, approver
):
    """Both directions, so the refusal is about the proposer, not the account.

    Run one way only, a check that hard-coded either holder would pass.
    """
    assert create_fault(client, proposer, "OWN-PROPOSAL").status_code == 200
    fault = fault_named(db_session, "OWN-PROPOSAL")

    res = approve_fault(client, proposer, fault.id)
    assert res.status_code == 403
    assert "proposed" in res.json()["detail"]
    db_session.refresh(fault)
    assert fault.is_pending is True

    # Still in the queue the proposer reads, for someone else to approve.
    assert "OWN-PROPOSAL" in {f["name"] for f in pending_faults(client, proposer).json()}

    assert approve_fault(client, approver, fault.id).status_code == 200
    db_session.refresh(fault)
    assert fault.is_pending is False


def test_the_rule_covers_a_type_minted_by_a_fault_report(
    client, db_session, mock_matrix_db
):
    """The side door: report_fault mints types too.

    u_master holds MANAGE_CATALOG and REPORT_STATUS over the root. Before
    SEC-M14 their reported type skipped review outright; a rule that guarded
    only the setup route's rows would let them report a fault, then approve
    the type the report minted.
    """
    item = item_named(db_session, "SA100")
    assert report(client, "u_master", item.id, fault="REPORTED-NOVEL").status_code == 200
    fault = fault_named(db_session, "REPORTED-NOVEL")
    assert fault.is_pending is True
    assert fault.requested_by_id == user_id(db_session, "u_master")

    assert approve_fault(client, "u_master", fault.id).status_code == 403
    db_session.refresh(fault)
    assert fault.is_pending is True

    assert approve_fault(client, "u_brig_cmdr", fault.id).status_code == 200
    db_session.refresh(fault)
    assert fault.is_pending is False


def test_the_proposer_is_refused_even_once_the_type_is_approved(
    client, db_session, mock_matrix_db
):
    """Unconditional, on purpose: the proposer never approves.

    Approving an approved type changes nothing, so a carve-out for it would be
    harmless today -- and one more branch for the next edit to get wrong.
    """
    assert create_fault(client, "u_master", "ALREADY-APPROVED").status_code == 200
    fault = fault_named(db_session, "ALREADY-APPROVED")
    assert approve_fault(client, "u_brig_cmdr", fault.id).status_code == 200

    assert approve_fault(client, "u_master", fault.id).status_code == 403
    db_session.refresh(fault)
    assert fault.is_pending is False


@pytest.mark.parametrize("who", HOLDERS)
def test_a_type_with_no_proposer_is_approvable(client, db_session, mock_matrix_db, who):
    """requested_by_id NULL, as on every seeded type, compares equal to nobody."""
    fault = a_fault(db_session, name="SEEDED-SHAPE")
    assert fault.requested_by_id is None

    assert approve_fault(client, who, fault.id).status_code == 200
    db_session.refresh(fault)
    assert fault.is_pending is False


def test_a_missing_id_is_still_a_404_for_a_holder(client, db_session, mock_matrix_db):
    """The proposer check sits after the lookup; before it, None has no proposer."""
    assert approve_fault(client, "u_master", 999_999).status_code == 404


def test_a_proposal_is_stamped_with_its_proposer(client, db_session, mock_matrix_db):
    """The rule reads requested_by_id, so a route that stopped writing it --
    or wrote someone else -- would turn the two-person rule off silently."""
    assert create_fault(client, "u_brig_cmdr", "STAMPED").status_code == 200
    fault = fault_named(db_session, "STAMPED")

    assert fault.is_pending is True
    assert fault.requested_by_id == user_id(db_session, "u_brig_cmdr")


# --- structural: two routes mint, one approves ------------------------------


def _router_trees():
    for path in sorted(ROUTERS.glob("*.py")):
        yield path.name, ast.parse(path.read_text(encoding="utf-8"))


def _is_fault_type_ctor(node):
    """models.FaultType(...) or a bare FaultType(...) imported from models.

    Not any attribute named FaultType: schemas.FaultType would be a response
    model one day, and constructing one writes nothing.
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "FaultType"
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "FaultType"
        and isinstance(func.value, ast.Name)
        and func.value.id == "models"
    )


def _targets(node):
    """An assignment's targets, with tuple and list unpacking opened up."""
    pending = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
    while pending:
        target = pending.pop()
        if isinstance(target, (ast.Tuple, ast.List)):
            pending.extend(target.elts)
        else:
            yield target


def _string_statements(tree):
    """Ids of the constants that are bare statements: docstrings, mostly."""
    return {
        id(node.value) for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }


def test_every_fault_type_a_router_creates_is_pending():
    """Each FaultType(...) in backend/routers passes the literal is_pending=True.

    Literal, not merely truthy: `is_pending=not is_manager` is the exact shape
    both routes had before SEC-M14, and it is True for most callers. seed_data
    is out of scope on purpose -- seeded vocabulary is approved by definition.
    """
    found = []
    for name, tree in _router_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_fault_type_ctor(node):
                pending = [k.value for k in node.keywords if k.arg == "is_pending"]
                found.append((name, node.lineno))
                assert len(pending) == 1, f"{name}:{node.lineno} does not set is_pending"
                assert isinstance(pending[0], ast.Constant) and pending[0].value is True, (
                    f"{name}:{node.lineno} creates a fault type that is not pending. "
                    "Every new type waits for a second MANAGE_CATALOG holder (SEC-M14)."
                )

    # Not vacuous: the two routes that mint a fault type.
    assert sorted(n for n, _ in found) == ["maintenance.py", "setup.py"], found


def _writes_is_pending(node, prose):
    """Whether this one node writes the flag, by the routes the ORM offers.

    An attribute assignment (`fault.is_pending = False`); a bulk write through
    `.update(...)` or `.values(...)`, by mapping (`{FaultType.is_pending: x}`
    or the string key) or by keyword; a setattr call; and raw SQL updating the
    table. Not a bare dict: the routes build response dicts with an
    "is_pending" key, which read the flag. Not a docstring that mentions the
    SQL (`prose`). Constructors are not here either: the test above holds
    those to the literal True. Not covered: bulk_update_mappings, and a write
    whose attribute name is computed.
    """
    if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        return any(
            isinstance(t, ast.Attribute) and t.attr == "is_pending" for t in _targets(node)
        )
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("update", "values"):
            if any(k.arg == "is_pending" for k in node.keywords):
                return True
            return any(
                (isinstance(k, ast.Constant) and k.value == "is_pending")
                or (isinstance(k, ast.Attribute) and k.attr == "is_pending")
                for arg in node.args if isinstance(arg, ast.Dict)
                for k in arg.keys
            )
        if getattr(func, "id", None) == "setattr":
            return any(isinstance(a, ast.Constant) and a.value == "is_pending" for a in node.args)
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in prose:
        return re.search(r"update\s+fault_types\b", node.value, re.IGNORECASE) is not None
    return False


def test_only_approve_fault_type_clears_the_pending_flag():
    """In the routers, one function writes is_pending after creation: the one
    with the proposer check.

    Reported per enclosing function, so a write that moves within setup.py
    still fails; module-level code is reported as "<module>". Scope is
    backend/routers, where the requests land: a helper module that wrote the
    flag on a route's behalf would pass here.
    """
    found = set()
    for name, tree in _router_trees():
        prose = _string_statements(tree)
        scopes = [("<module>", tree)] + [
            (n.name, n) for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for scope, root in scopes:
            nodes = ast.walk(root) if scope != "<module>" else (
                node for stmt in root.body
                if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                for node in ast.walk(stmt)
            )
            if any(_writes_is_pending(node, prose) for node in nodes):
                found.add((name, scope))

    assert found == {("setup.py", "approve_fault_type")}, (
        "is_pending is written outside setup.approve_fault_type, which is "
        f"where the two-person rule lives (SEC-M14): {sorted(found)}"
    )
