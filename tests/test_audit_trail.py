"""DATA-H4-1: one writer for the audit tables, and nothing that can go round it.

backend/audit_trail.py's docstring carries the census of what was wrong; this
file does not repeat it, so the two did not drift as DATA-H4-2 and -3 changed
the counts.

The ticket's fix is "route every state mutation through one audit-writing
helper so no path CAN bypass it". The first three tests here are that sentence
-- one per audit table construction, one per owned column. Everything below
them is the behaviour the helper is supposed to have.

DATA-H4-3 is where "every state mutation" stopped being aspirational: every
route under backend/routers that mutates an equipment row now writes at least
one audit row, and the two columns that decide readiness and visibility are
both assignable in exactly one file.

WHY THE GUARDS ARE AST WALKS AND NOT REVIEW
--------------------------------------------
The four divergent call sites WERE a convention, followed by every author who
touched them and wrong anyway. A fifth site added next month is refused by a
failing test or by nothing; there is no third option, and tests/test_utc_contract
.py:31-64 already makes exactly this argument for datetime.utcnow() ("the ONLY
defence against partial application"). This file copies its shape deliberately.
"""
import ast
import functools
import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

import backend
from backend import audit_trail, models
from backend.enums import ChangeReason, EventType
from tests.conftest import create_auth_header

BACKEND_ROOT = Path(backend.__file__).parent
WRITER = "audit_trail.py"

AUDIT_TABLES = ("TransactionLog", "EquipmentStatusHistory")

# A site that moves a status outside the writer marks the line that does it:
#
#     ).update({"status": "Closed", ...})  # audit-trail-bypass: permanent
#
# DATA-H4-1 left two of these carrying `DATA-H4-2`, on report_fault's and
# fix_equipment's bare assignments; DATA-H4-2 cut both routes onto the helper
# and deleted them. The one that remains is fix_equipment's ticket-closing bulk
# update, and it is permanent -- a ticket's Open/Closed is not an equipment
# status and equipment_status_history has no row shape for it.
#
# DATA-H4-3 added a second guarded column and no fourth pragma: set_sensitivity
# had one assignment and it moved into the writer whole. A guard arriving with
# nothing to waive is the cheap case, and the reason to add it then rather than
# after a second site exists.
#
# A pragma rather than a (file, function) tuple listed here, and the difference
# is not cosmetic. A function-keyed waiver exempts the whole FUNCTION: an
# author cutting report_fault onto the helper who left one stray assignment
# behind would keep it hidden, and a staleness check comparing sets could not
# see it either. It also breaks silently on rename, and worse, silently
# TRANSFERS to any new function that reuses the name. The pragma travels with
# the statement, is visible where the bypass is, and cannot drift.
BYPASS_PRAGMA = "audit-trail-bypass:"


@functools.lru_cache(maxsize=1)
def backend_sources():
    """Every source file under backend/, as (relative posix path, text, tree).

    Cached, and read ONCE. Five separate walks over backend/ had grown up in
    this file -- the two AST guards, the two writer guards, and the pragma
    lookup, each globbing and re-reading the same tree, with the pragma lookup
    re-reading a file whose text backend_modules had just discarded. Nothing was
    slow enough to notice; it is one line to stop doing, and a guard that reads
    the source twice can read two different versions of it.
    """
    return tuple(
        (
            path.relative_to(BACKEND_ROOT).as_posix(),
            source,
            ast.parse(source, filename=str(path)),
        )
        for path, source in (
            (p, p.read_text(encoding="utf-8"))
            for p in sorted(BACKEND_ROOT.rglob("*.py"))
        )
    )


def backend_modules():
    """The (path, tree) view, for guards that do not need the text."""
    for relpath, _source, tree in backend_sources():
        yield relpath, tree


def _names_column(key, column):
    """Is this dict key the named column, in either spelling?

    `{"status": x}` is an ast.Constant; `{models.Equipment.status: x}` is an
    ast.Attribute and is the more idiomatic SQLAlchemy of the two. Matching
    only the string was a hole a reviewer walked straight through.
    """
    if isinstance(key, ast.Constant):
        return key.value == column
    return isinstance(key, ast.Attribute) and key.attr == column


def column_assignments(tree, column):
    """Every statement that writes the named equipment column, as AST nodes.

    Parameterised by DATA-H4-3, which needed the identical walk for
    `.sensitivity`. The alternative was a forty-line copy differing in one
    string literal, in the file whose entire subject is what happens when the
    same logic is written out more than once.

    TWO SHAPES, because Python and SQLAlchemy each offer one.

    The examples below are all `status`, because that is the pair of shapes
    the guard was built against and both are attested there. They read the same
    with `sensitivity` substituted; nothing about either shape is specific to
    which column is named.

    The first is any attribute store -- `item.status = x`. Detected by asking
    the parser for `ast.Store` context rather than by enumerating statement
    types, which is both shorter and stricter than the hand-rolled version this
    replaces: that one triaged Assign/AnnAssign/AugAssign and recursed through
    tuple targets, and still missed `for item.status in xs:` and
    `with x as item.status:`, both of which are stores. `ctx` is what the
    parser already computed; re-deriving it was the bug.

    Reads keep `ast.Load`, so `counts[log.status] += 1` and `x = item.status`
    are not hits -- verified against both.

    The second is `query(...).update({"status": ...})`, which issues UPDATE
    without ever touching an attribute. Not hypothetical: fix_equipment uses
    exactly this idiom on MaintenanceLog four lines from its own status write,
    so it is live house style, and an author reaching for it on equipment would
    move a status past a guard that only watched attributes.

    EVERY model, not just Equipment. An earlier version required the name
    `Equipment` inside the call's own receiver, which a reviewer defeated in
    one line by binding the query to a variable first -- `q = db.query(Equipment)`
    then `q.update({...})` -- since the receiver is then just `q`. Static
    analysis cannot follow that binding, so the guard stops trying to guess the
    model and asks about the COLUMN instead. The cost is that closing a ticket
    needs a pragma; that pragma is honest documentation, not noise, because a
    bulk status update genuinely is a status change the audit trail does not
    cover.

    WHAT THIS DOES NOT CATCH, stated plainly because the alternative is a false
    promise: `setattr(item, "status", x)`, an aliased class
    (`Log = models.TransactionLog`), `getattr(models, name)(...)`, and raw
    `text("UPDATE equipment SET ...")`. Each is deliberate circumvention rather
    than a plausible slip, and no static check short of running the program
    closes them all. The guard's claim is that no ORDINARY spelling gets past
    it, not that evasion is impossible.
    """
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == column
            and isinstance(node.ctx, ast.Store)
        ):
            yield node
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
        ):
            for argument in node.args:
                if isinstance(argument, ast.Dict) and any(
                    _names_column(key, column) for key in argument.keys
                ):
                    yield node


def map_keys(source, marker):
    """The keys of a `const NAME: Record<...> = {...}` literal in a .ts/.tsx file.

    Anchored to the start of a line, which is the point rather than fussiness.
    An earlier version asked `f"{value}: {{" in table`, and a mutation test
    walked through it twice: `xfault: {` CONTAINS `fault: {`, so a typo'd key
    satisfied the check -- and so would the realistic case, a key named in a
    COMMENT (`// fault: {...} coming in H4-3`) while the entry itself is absent.
    A guard whose whole job is to stop a member landing a commit ahead of its
    label cannot count a mention as an entry.

    The same hole was in DATA-H4-1's dashboard guard from the day it was
    written, for the same reason its sibling needed `\\b` added: a substring test
    over a region of source answers a question about TEXT when the question is
    about STRUCTURE. Both guards read this now, so there is one answer.

    The DECLARATION is located by matching `marker ... = {`, not by splitting on
    the marker text. Splitting broke the moment a comment in the same file
    mentioned `const REASON_META` in prose: with two occurrences,
    `split(marker)[1]` returns the text BETWEEN them, the key scan saw an empty
    body, and every reason reported as missing. That failure was loud, but the
    same shape rotated one way round is silent -- prose after the real
    declaration would have extended the body, not truncated it.

    Block comments are stripped before the scan, because line-anchoring alone
    does not survive them: a key commented out inside a `/* ... */` sits at the
    start of its own line and would otherwise read as an entry. Line comments
    need no stripping -- the `//` is what fails the anchor.

    Still not a parser, deliberately -- it does not know a key inside a nested
    object from a top-level one. It knows a key from prose, which is the
    distinction the guards actually turn on, and which it has now got wrong
    twice in two different ways.
    """
    declaration = re.search(re.escape(marker) + r"[^=\n]*=\s*\{", source)
    assert declaration, f"no `{marker} ... = {{` declaration in this file"

    body = source[declaration.end():].split("};")[0]
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    return set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*\{", body, re.MULTILINE))


def enclosing_function(tree, target):
    """The name of the def containing `target`, for a legible failure message."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            child is target for child in ast.walk(node)
        ):
            return node.name
    return "<module>"


# --- 1. The three guards ----------------------------------------------------


def test_only_audit_trail_constructs_an_audit_row():
    """models.TransactionLog(...) and TransactionLog(...) both live in one file.

    Parsed rather than grepped so that models.py's own `class TransactionLog`
    is not a hit -- a ClassDef is not a Call -- and so this docstring naming
    the symbols cannot fail its own test.

    BOTH SPELLINGS, and the second one is not hypothetical: an earlier version
    of this guard matched only `ast.Attribute`, and a probe that added
    `from ..models import TransactionLog` to a router then constructed it bare
    passed the whole file. A guard with a known way around it is worse than no
    guard, because the module docstring next door claims no path CAN bypass it.

    So the import is refused as well as the call. Refusing the import is the
    stronger half -- a name that cannot be bound cannot be called -- and the
    bare-Call check below it covers a class arriving by any other route.
    """
    offenders = []

    for relpath, tree in backend_modules():
        if relpath == WRITER:
            continue
        for node in ast.walk(tree):
            # `from .models import TransactionLog` -- the setup, not the use.
            if isinstance(node, ast.ImportFrom) and node.module and "models" in node.module:
                for alias in node.names:
                    if alias.name in AUDIT_TABLES:
                        offenders.append(
                            f"{relpath}:{node.lineno} imports {alias.name} directly"
                        )
            if not isinstance(node, ast.Call):
                continue
            # models.TransactionLog(...) / m.TransactionLog(...)
            if isinstance(node.func, ast.Attribute) and node.func.attr in AUDIT_TABLES:
                offenders.append(
                    f"{relpath}:{node.lineno} in "
                    f"{enclosing_function(tree, node)}() -> {node.func.attr}"
                )
            # TransactionLog(...) -- bare, however the name got here.
            elif isinstance(node.func, ast.Name) and node.func.id in AUDIT_TABLES:
                offenders.append(
                    f"{relpath}:{node.lineno} in "
                    f"{enclosing_function(tree, node)}() -> {node.func.id}"
                )

    assert offenders == [], (
        "an audit row is constructed outside backend/audit_trail.py, which is "
        f"the bypass DATA-H4 exists to close: {offenders}"
    )


def assignments_outside_the_writer(column):
    """Every unwaived write of `column` in backend/, outside audit_trail.py."""
    offenders = []

    for relpath, source, tree in backend_sources():
        if relpath == WRITER:
            continue
        lines = source.splitlines()
        for node in column_assignments(tree, column):
            if BYPASS_PRAGMA in lines[node.lineno - 1]:
                continue
            offenders.append(
                f"{relpath}:{node.lineno} in {enclosing_function(tree, node)}()"
            )

    return offenders


def test_only_audit_trail_assigns_equipment_status():
    """`something.status = x` happens in one file, plus a waiver DATA-H4-2 empties.

    The construction guard above is only half the claim. A route that calls
    record_event and then assigns .status itself has produced a movement-log
    entry and no status history -- which is precisely what fix_equipment did
    before this ticket, so the failure mode is attested rather than imagined.

    Constructor kwargs are silent here: seed_data.py's `status="Functional"` is
    an ast.keyword on a Call, it creates a row rather than transitions one, and
    there is no prior status for a history row to record.

    An intended bypass marks its own line with the pragma above. The one that
    exists is fix_equipment's ticket-closing bulk update, which is permanent.

    The match is on the NAME `status`, with no idea whose. An unrelated class
    doing `self.status = "ok"` under backend/ would be flagged, and the right
    repair then is to narrow this guard by target -- not to pragma correct
    code, which is how a guard becomes noise and then becomes deleted.
    """
    offenders = assignments_outside_the_writer("status")

    assert offenders == [], (
        "equipment status is assigned outside audit_trail.set_status, so a "
        f"status can move with no history row explaining it: {offenders}"
    )


def test_only_audit_trail_assigns_equipment_sensitivity():
    """The same claim for the other owned column. DATA-H4-3.

    Sensitivity earned a guard for a different reason than status did. Status
    had five call sites that had already diverged; sensitivity had exactly one,
    and one site cannot have drifted from itself. What it had instead was no
    audit at all -- an item could be classified or declassified through the API
    and the movement report showed nothing, at a column that since DATA-H3-3
    decides who may see the item.

    So this guard is not preserving a hard-won consolidation; it is stopping the
    second site from ever being written inline, which is how the status guard
    came to have five to consolidate. There are no pragmas because there is
    nothing to waive: set_sensitivity's assignment moved into the writer and
    nothing else under backend/ writes the column.

    create_equipment is invisible to this and correctly so. It passes
    sensitivity as a constructor kwarg -- an ast.keyword, not a Store -- which
    creates a row rather than reclassifying one, and its audit is EventType
    .CREATE. A creation that also happens to be classified is one event, not two.
    """
    offenders = assignments_outside_the_writer("sensitivity")

    assert offenders == [], (
        "equipment sensitivity is assigned outside audit_trail.set_sensitivity, "
        "so an item's classification can change with nothing in the movement "
        f"report saying who changed it: {offenders}"
    )


# The two guards above assert `offenders == []` against the real backend/ tree,
# and after DATA-H4-3 that tree contains no positive case for either column:
# every attribute store lives in the writer, the one bulk update is waived, and
# no .update({"sensitivity": ...}) exists at all. So they pass whether or not
# column_assignments detects anything, which makes them a detector nobody has
# watched detect. Found by review, then confirmed by mutation -- gutting
# column_assignments to yield nothing, and pinning _names_column to "status" so
# the sensitivity guard silently degrades into a second status guard, BOTH left
# the suite green.
#
# The battery that should have caught it mutated backend/, where a guard with
# no live violation to find cannot fail. These plant the violation instead, and
# are the only tests here that assert the guard says YES.
SYNTHETIC = {
    "attribute store": "item.{col} = 'x'",
    "augmented store": "item.{col} += 'x'",
    # Both named in column_assignments' docstring as shapes the ast.Store test
    # catches and the hand-rolled predecessor missed. Untested until now.
    "for-loop target": "for item.{col} in xs:\n    pass",
    "with-as target": "with ctx() as item.{col}:\n    pass",
    "bulk update, string key": 'db.query(M).filter(c).update({{"{col}": "x"}})',
    "bulk update, column key": "db.query(M).update({{models.Equipment.{col}: 'x'}})",
}


def test_every_setter_declares_the_column_it_owns():
    """OWNED_COLUMNS cannot fall behind the setters it is supposed to describe.

    The guards below are parametrized over audit_trail.OWNED_COLUMNS, so a
    `set_priority` added to the writer without a matching entry there would be
    policed by nothing at all -- and every existing test would stay green,
    because none of them is about the column that went unguarded. That is this
    module's own failure mode ("no path CAN bypass it") reappearing one level
    up: not whether a column's writes are checked, but which columns get
    checked.

    Derived from the writer's own `def set_<column>` names rather than from a
    second list, so the two cannot disagree. record_event is deliberately not
    matched -- it owns no column, which is why it is not spelled `set_`.
    """
    tree = ast.parse((BACKEND_ROOT / WRITER).read_text(encoding="utf-8"))
    setters = {
        node.name[len("set_"):]
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("set_")
    }

    assert setters == set(audit_trail.OWNED_COLUMNS), (
        f"audit_trail defines setters for {sorted(setters)} but declares "
        f"{sorted(audit_trail.OWNED_COLUMNS)}; a column with a setter and no "
        "declaration is guarded by nothing"
    )


@pytest.mark.parametrize("column", audit_trail.OWNED_COLUMNS)
@pytest.mark.parametrize("shape", sorted(SYNTHETIC), ids=lambda s: s.replace(" ", "_"))
def test_the_guard_detects_a_planted_write(column, shape):
    """Every shape the guard claims to catch, caught. Both columns."""
    source = SYNTHETIC[shape].format(col=column)
    found = list(column_assignments(ast.parse(source), column))

    assert found, (
        f"column_assignments missed a {shape} of .{column} -- the guard that "
        f"is supposed to refuse this shape would report a clean tree: {source!r}"
    )


@pytest.mark.parametrize("column", audit_trail.OWNED_COLUMNS)
def test_the_guard_reads_the_column_it_is_asked_about(column):
    """The generalisation is real, not a status guard wearing a parameter.

    Kills the mutation that pins _names_column to "status": under it the
    sensitivity guard still returns [] on the real tree, because its one false
    hit lands on the bulk update that the pragma strips anyway. Asked directly,
    the degradation is obvious.
    """
    other = "sensitivity" if column == "status" else "status"
    source = (
        f"item.{other} = 'x'\n"
        f"db.query(M).update({{'{other}': 'x'}})"
    )

    assert list(column_assignments(ast.parse(source), column)) == [], (
        f"asking about .{column} reported a write to .{other}; the guard is "
        "not actually reading its column argument"
    )


@pytest.mark.parametrize("column", audit_trail.OWNED_COLUMNS)
def test_the_guard_ignores_reads(column):
    """ast.Load must stay invisible, or the guard flags correct code.

    A guard that fires on `x = item.status` gets pragmas scattered over
    innocent lines until somebody deletes it -- the failure mode
    column_assignments' docstring names as how a guard becomes noise.
    """
    source = (
        f"x = item.{column}\n"
        f"counts[log.{column}] += 1\n"
        f"send(schemas.Response({column}=item.{column}))"
    )

    assert list(column_assignments(ast.parse(source), column)) == [], (
        f"a READ of .{column} was reported as a write"
    )


# --- 2. assign_owner, the ticket's headline ---------------------------------


def item_named(db_session, serial):
    return db_session.query(models.Equipment).filter_by(serial_number=serial).one()


def test_assigning_ownership_writes_a_log_that_reaches_the_movement_report(
    client, db_session, mock_matrix_db
):
    """A change of custody was the one event that produced no record at all.

    Asserted through /reports/daily_movement rather than by counting rows,
    because "writes a row" was never the complaint -- DATA-H4's words are
    "produces no record and NEVER APPEARS IN THE MOVEMENT REPORT". A row the
    report cannot render would satisfy a count and not the ticket.
    """
    item = item_named(db_session, "SA100")
    target = mock_matrix_db["company_tech_a"]

    res = client.post(
        "/equipment/assign_owner/",
        json={"equipment_id": item.id, "owner_id": target.id},
        headers=create_auth_header("u_cmdr_a"),
    )
    assert res.status_code == 200, res.text

    report = client.get(
        "/reports/daily_movement", headers=create_auth_header("u_cmdr_a")
    ).json()
    rows = [r for r in report if r["serial_number"] == "SA100"]

    assert len(rows) == 1, f"expected exactly one movement row, got {rows}"
    assert rows[0]["event_type"] == "ASSIGN"
    assert rows[0]["location"] == f"User:{target.full_name}", (
        "the new owner must be recoverable from the row; involved_user_id "
        "records the ACTOR, so this column is the only place they appear"
    )
    assert rows[0]["timestamp"] is not None


def test_a_refused_assignment_writes_no_log(client, db_session, mock_matrix_db):
    """The gate is above the write, not below it.

    Company B's item, Company A's commander: a 403. Logging an ASSIGN for every
    attempt would put events that never happened into the item's history, which
    is the record an investigation reads. Counted rather than read back through
    the report, because the refused caller cannot see that item's rows at all
    and an empty report would pass whether or not a row was written.
    """
    item = item_named(db_session, "SB200")
    before = db_session.query(models.TransactionLog).count()

    res = client.post(
        "/equipment/assign_owner/",
        json={"equipment_id": item.id, "owner_id": mock_matrix_db["soldier_a"].id},
        headers=create_auth_header("u_cmdr_a"),
    )
    assert res.status_code == 404, res.text

    assert db_session.query(models.TransactionLog).count() == before


# --- 3. set_status -----------------------------------------------------------


def test_a_verification_that_moves_nothing_writes_no_history_row(
    client, db_session, mock_matrix_db
):
    """old_status == new_status asserts a transition that did not happen.

    SA100 is already Functional in the fixture, so verifying it as Functional
    is a real call that changes nothing. The Verification row is still written
    -- somebody did look at the item -- and that is the distinction the two
    tables draw: verifications record the ACT, history records the CHANGE.

    DATA-H4-3 added the third assertion, and it is where this test stopped
    describing a clean division and started describing a hole. Until then this
    call wrote NO row into EITHER audit table: history correctly declined a
    transition that did not happen, and nothing recorded that anyone had looked.
    An inspection with a null result is still an inspection, and the audit trail
    said nothing had occurred. CONDITION_REPORT is written unconditionally for
    exactly this case.
    """
    item = item_named(db_session, "SA100")
    assert item.status == "Functional", "fixture precondition"

    res = client.post(
        "/verifications/",
        json={
            "equipment_id": item.id,
            "verification_type": "daily",
            "reported_status": "Functional",
            "findings": "all good",
            "action_required": False,
        },
        headers=create_auth_header("u_cmdr_a"),
    )
    assert res.status_code == 200, res.text

    assert db_session.query(models.EquipmentStatusHistory).filter_by(
        equipment_id=item.id
    ).count() == 0
    assert db_session.query(models.Verification).filter_by(
        equipment_id=item.id
    ).count() == 1
    assert db_session.query(models.TransactionLog).filter_by(
        equipment_id=item.id, event_type=EventType.CONDITION_REPORT.value
    ).count() == 1, (
        "a verification that confirmed the status wrote nothing into either "
        "audit table -- somebody inspected the item and the trail is silent"
    )


def test_a_status_change_records_both_ends_and_its_verification(
    client, db_session, mock_matrix_db
):
    """The row the refactor had to keep producing identically.

    verification_id is the assertion that matters most here: it is only
    available because create_verification flushes before calling set_status,
    and a refactor that moved the call above that flush would write a null
    without failing anything else.
    """
    item = item_named(db_session, "SA100")

    res = client.post(
        "/verifications/",
        json={
            "equipment_id": item.id,
            "verification_type": "daily",
            "reported_status": "Malfunctioning",
            "findings": "cracked stock",
            "action_required": True,
        },
        headers=create_auth_header("u_cmdr_a"),
    )
    assert res.status_code == 200, res.text

    history = db_session.query(models.EquipmentStatusHistory).filter_by(
        equipment_id=item.id
    ).one()
    assert (history.old_status, history.new_status) == ("Functional", "Malfunctioning")
    assert history.change_reason == "verification"
    assert history.notes == "cracked stock"
    assert history.created_by == mock_matrix_db["company_cmdr_a"].id
    assert history.verification_id == res.json()["id"], (
        "the history row lost its link to the verification that caused it"
    )


# --- 4. record_event's normalisation ----------------------------------------


@pytest.mark.parametrize(
    "path,body,expected",
    [
        ("/equipment/transfer", {"to_holder_id": "TECH"}, "HANDOVER"),
        ("/equipment/transfer", {"to_location": "Armory"}, "HANDOVER_LOC"),
    ],
    ids=["handover_person", "handover_location"],
)
def test_transfer_still_writes_the_event_type_it_always_wrote(
    client, db_session, mock_matrix_db, path, body, expected
):
    """The stored strings are frozen, not merely current.

    A database that has been running carries these literals, and
    test_group_schema.py inserts 'HANDOVER' by raw SQL and asserts it survives
    the migration chain. Moving the constructions into an enum was free only
    because the VALUES did not change; this is what says so.
    """
    item = item_named(db_session, "SA100")
    payload = {"equipment_id": item.id}
    payload.update(
        {k: (mock_matrix_db["company_tech_a"].id if v == "TECH" else v)
         for k, v in body.items()}
    )

    res = client.post(path, json=payload, headers=create_auth_header("u_cmdr_a"))
    assert res.status_code == 200, res.text

    log = db_session.query(models.TransactionLog).filter_by(
        equipment_id=item.id
    ).one()
    assert log.event_type == expected


def test_every_event_records_whether_the_actor_was_on_duty(
    client, db_session, mock_matrix_db
):
    """fix_equipment was the site that did not, and the reason record_event normalises.

    user_status_at_time is read by no endpoint today, so nothing else in the
    suite would notice it going missing -- and nothing noticed for as long as
    one write in four omitted it. A column that answers the question for three
    event types and not the fourth answers it for none.
    """
    item = item_named(db_session, "SA100")

    assert client.post(
        "/maintenance/report",
        json={"equipment_id": item.id, "fault_name": "Cracked", "description": "x"},
        headers=create_auth_header("u_cmdr_a"),
    ).status_code == 200

    assert client.post(
        f"/maintenance/fix/{item.id}", headers=create_auth_header("u_tech_a")
    ).status_code == 200

    logs = db_session.query(models.TransactionLog).filter_by(
        equipment_id=item.id
    ).all()
    fix = [log for log in logs if log.event_type == "FIX"]

    assert len(fix) == 1, "the fix event is missing"
    assert fix[0].user_status_at_time is True
    assert fix[0].timestamp is not None
    assert fix[0].involved_user_id == mock_matrix_db["company_tech_a"].id


# --- 5. The contracts the module docstring claims -----------------------------


FRONTEND_SRC = BACKEND_ROOT.parent / "frontend/src"
DASHBOARD_TABLE = (
    FRONTEND_SRC / "features/dashboard/components/DailyActivityTable.tsx"
)
REASON_META_FILE = FRONTEND_SRC / "features/equipment/changeReasons.ts"


def test_the_writer_never_commits():
    """"Adds, never commits" is the sentence both helpers open with.

    It is the reason a refused write leaves no row and a rolled-back mutation
    takes its audit row with it: the audit row and the change it describes
    share one transaction because the helper declines to own one. A stray
    db.commit() here would silently decouple them, and every existing
    "leaves no row" test would still pass -- those exercise refusals ABOVE the
    write, which never reach this code at all.

    A flush would be subtler and is refused for the same reason: it makes the
    row visible to the rest of the transaction and to any nested savepoint
    logic a future caller adds.
    """
    tree = ast.parse((BACKEND_ROOT / WRITER).read_text(encoding="utf-8"))
    offenders = [
        f"{WRITER}:{node.lineno} calls .{node.func.attr}()"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("commit", "flush")
    ]
    assert offenders == [], (
        f"the audit writer owns a transaction it must not own: {offenders}"
    )


def test_a_rolled_back_change_takes_its_audit_row_with_it(db_session, mock_matrix_db):
    """The other half of the same contract, proven rather than reasoned.

    Exercised at the session rather than through a route because no route
    currently fails between the audit write and the commit -- which is exactly
    why this needs pinning now. transfer_equipment's `except Exception` calls
    db.rollback(), and the guarantee that the rollback reaches the audit row is
    what makes that handler safe.
    """
    item = item_named(db_session, "SA100")
    before = db_session.query(models.TransactionLog).count()

    audit_trail.record_event(
        db_session,
        equipment=item,
        actor=mock_matrix_db["master"],
        event_type=EventType.ASSIGN,
    )
    db_session.rollback()

    assert db_session.query(models.TransactionLog).count() == before


def test_every_event_type_has_a_writer():
    """enums.py's own rule, enforced instead of asserted.

    "Every member here is written by a router, and each arrived in the same
    commit as the route that writes it" is a claim EventType's docstring makes
    in prose, borrowed from Capability's, which exists because SEC-H4 declared
    six permissions that no router ever read. A vocabulary that outruns its
    writers is that defect in a smaller key, and DATA-H4-2 and -3 each add
    members -- this is what stops one landing a commit early.
    """
    sources = "\n".join(
        source for relpath, source, _tree in backend_sources()
        if relpath != "enums.py"
    )
    # \b, not a plain substring. `EventType.HANDOVER` occurs inside
    # `EventType.HANDOVER_LOC`, so a plain `in` check reported HANDOVER as
    # written even with its only writer deleted -- verified by deleting it.
    # Every prefix pair in this enum would have inherited that hole.
    orphans = [
        e.name
        for e in EventType
        if not re.search(rf"\bEventType\.{e.name}\b", sources)
    ]

    assert orphans == [], (
        f"EventType members that no code writes: {orphans}. Declare a member in "
        "the commit that starts emitting it, not before."
    )


def test_every_event_type_renders_in_the_dashboard():
    """The cross-stack half, and the bug that actually shipped.

    The backend has emitted HANDOVER since the beginning while
    DailyActivityTable's switch knew only 'movement' and 'transfer', so every
    handover in the system fell through to `default` and printed raw English
    into an RTL Hebrew table -- for as long as the feature has existed, with a
    green suite throughout, because nothing connected the two files.

    Reads the EVENT_META map rather than a switch body, which is why the map
    replaced three switches: an entry carries icon, colour AND label together,
    so this one check covers all three. It could not before -- it sliced on
    `function getEventLabel`, and VERIFICATION had a label with no icon or
    colour arm, invisible to a labels-only guard.

    Keys are extracted by map_keys rather than substring-scanned; see there for
    the two ways the substring version could be satisfied without an entry
    existing.
    """
    assert DASHBOARD_TABLE.exists(), f"dashboard table moved: {DASHBOARD_TABLE}"
    keys = map_keys(DASHBOARD_TABLE.read_text(encoding="utf-8"), "const EVENT_META")

    missing = [e.value for e in EventType if e.value.lower() not in keys]

    assert missing == [], (
        f"event types the dashboard cannot label: {missing}. They render as raw "
        "English in a Hebrew RTL table; add the arm in the commit that emits them."
    )


def test_every_change_reason_has_a_writer():
    """The same rule for the other enum, which DATA-H4-2 gave two new members.

    ChangeReason shipped in DATA-H4-1 with exactly one member for exactly one
    writer, and the temptation the whole time was to declare fault_report and
    repair alongside it -- the frontend already RENDERED both, which makes the
    vocabulary look agreed and is precisely the argument SEC-H4 lost. A rendered
    label for a value no router emits is a UI that describes a system that does
    not exist. Enforced rather than remembered.

    No \\b needed here as it happens -- no member's name prefixes another's --
    but written the same way as EventType's deliberately, because that guard
    only needed it after a member was added that DID collide, and by then the
    hole had been open for a commit.
    """
    sources = "\n".join(
        source for relpath, source, _tree in backend_sources()
        if relpath != "enums.py"
    )
    orphans = [
        r.name
        for r in ChangeReason
        if not re.search(rf"\bChangeReason\.{r.name}\b", sources)
    ]

    assert orphans == [], (
        f"ChangeReason members that no code writes: {orphans}. Declare a member "
        "in the commit that starts emitting it, not before."
    )


def test_every_change_reason_renders_in_the_history_views():
    """The cross-stack half for equipment history, and the reason for the map.

    This vocabulary lived in THREE switches -- two in EquipmentHistory.tsx (icon
    and label separately) and a fused third in EquipmentPage.tsx's
    InlineHistory. They had already drifted: the inline copy said 'תקלה' where
    the modal said 'דיווח תקלה' for the identical row. A guard could not have
    caught that and could not have covered all three, which is why DATA-H4-2
    collapsed them into features/equipment/changeReasons.ts first and asserted
    afterwards.

    Reading the map means both views are covered by one check, permanently: a
    fourth view rendering history gets the translations for free rather than
    forking a fourth copy this test would not know to look at.
    """
    assert REASON_META_FILE.exists(), f"reason map moved: {REASON_META_FILE}"
    keys = map_keys(REASON_META_FILE.read_text(encoding="utf-8"), "const REASON_META")

    missing = [r.value for r in ChangeReason if r.value not in keys]

    assert missing == [], (
        f"change reasons the history views cannot label: {missing}. They fall "
        "through to the raw string in a Hebrew RTL list; add the entry in the "
        "commit that emits them."
    )


def test_no_history_view_keeps_its_own_reason_vocabulary():
    """The collapse has to STAY collapsed, or the guard above covers one file.

    Both consumers read the shared map, so the check above speaks for both. That
    holds exactly as long as nobody reintroduces a local switch -- and a local
    switch is the easy thing to write when adding one label in a hurry, which is
    how three of them accumulated here in the first place. A view with its own
    arms is invisible to the guard above while looking correct on screen.
    """
    # Derived from the enum, not hand-listed. The hand-listed version named
    # 'fault_report' and 'verification' and silently omitted 'repair' -- so a
    # partial revert reintroducing a switch for the one missing member passed,
    # which is the exact drift this guard exists to refuse. Both quote styles,
    # because `case "repair":` is the same defect the parser does not care about.
    arms = [
        f"case {quote}{r.value}{quote}:"
        for r in ChangeReason
        for quote in ("'", '"')
    ]
    strays = sorted(
        path.relative_to(FRONTEND_SRC).as_posix()
        for path, source in (
            (p, p.read_text(encoding="utf-8"))
            for p in FRONTEND_SRC.rglob("*.tsx")
        )
        if any(arm in source for arm in arms)
    )

    assert strays == [], (
        f"views switching on change_reason locally: {strays}. Read REASON_META "
        "from features/equipment/changeReasons.ts instead; a local switch is "
        "invisible to the completeness guard above."
    )


# --- 6. Adverse input --------------------------------------------------------


@pytest.mark.parametrize("corrupt", ["NULL", "''"], ids=["null", "empty_string"])
def test_an_absent_status_is_refused_however_it_is_spelled(
    client, db_session, mock_matrix_db, monkeypatch, corrupt
):
    """Both falsy spellings, because guarding one and not the other is the bug.

    NULL crashes loudly against a NOT NULL column; the empty string does not
    crash at all -- it satisfies the constraint and writes a history row
    claiming a transition out of nothing, quietly, into the table an
    investigation reads. The silent one is the worse outcome, and a guard
    written as `is None` catches only the loud one.

    That asymmetry is DATA-M1's defect exactly ("validation tests for explicit
    absence while the branch tests for truthiness"), catalogued elsewhere in
    this same audit -- so shipping it here would have meant reintroducing a
    known bug inside the fix for another one.

    Atomicity is asserted by counting COMMITS rather than rows, and that is not
    stylistic. create_verification flushes the Verification before it calls
    set_status, so after the refusal that row is visible in the session -- and
    conftest's StaticPool hands every Session the one same connection, so a
    second Session cannot tell flushed from committed either. Nothing in this
    harness can observe the difference by looking at rows. What production
    relies on is that no commit ran, leaving get_db's `finally: db.close()` to
    discard the transaction, so that is the thing worth asserting directly.
    """
    item = item_named(db_session, "SA100")
    db_session.execute(
        text(f"UPDATE equipment SET status = {corrupt} WHERE id = :id"), {"id": item.id}
    )
    db_session.commit()

    commits = []
    real_commit = db_session.commit
    monkeypatch.setattr(
        db_session, "commit", lambda: (commits.append(1), real_commit())[1]
    )

    res = client.post(
        "/verifications/",
        json={
            "equipment_id": item.id,
            "verification_type": "daily",
            "reported_status": "Malfunctioning",
            "findings": "x",
            "action_required": False,
        },
        headers=create_auth_header("u_cmdr_a"),
    )

    assert res.status_code == 409, res.text
    assert "equipment_status_history" not in res.text, "constraint name leaked"
    assert commits == [], (
        "the route committed before refusing, so get_db closing the session can "
        "no longer discard the partial write"
    )
    assert db_session.query(models.EquipmentStatusHistory).count() == 0


def test_verifying_an_unchanged_status_still_advances_the_clock(
    client, db_session, mock_matrix_db
):
    """set_status returning early must not swallow the caller's other work.

    last_verified_at is assigned AFTER the helper call in create_verification,
    and a refactor that tucked it inside the status-changed branch would make
    a clean daily verification stop counting as a verification -- silently
    degrading compliance for every item that is working correctly, which is
    most of them.
    """
    item = item_named(db_session, "SA100")
    db_session.execute(
        text("UPDATE equipment SET last_verified_at = :old WHERE id = :id"),
        {"old": "2020-01-01 00:00:00", "id": item.id},
    )
    db_session.commit()

    res = client.post(
        "/verifications/",
        json={
            "equipment_id": item.id,
            "verification_type": "daily",
            "reported_status": "Functional",
            "findings": None,
            "action_required": False,
        },
        headers=create_auth_header("u_cmdr_a"),
    )
    assert res.status_code == 200, res.text

    db_session.expire_all()
    refreshed = item_named(db_session, "SA100")
    assert refreshed.last_verified_at.year > 2020, "the verification clock stalled"
    assert db_session.query(models.EquipmentStatusHistory).count() == 0


@pytest.mark.parametrize("empty", [None, ""], ids=["none", "empty_string"])
def test_set_status_refuses_an_empty_new_status(db_session, mock_matrix_db, empty):
    """The caller's side of the absent-status rule, which had no test.

    Unreachable from any route today -- every caller passes an EquipmentStatus
    value or a literal -- and that is exactly why it is asserted here rather
    than trusted: this module exists to attract callers, DATA-H4-2 and -3 each
    add several, and an unguarded empty new_status writes a row claiming a
    transition INTO nothing while satisfying the NOT NULL column.

    ValueError, not the 409 its old_status counterpart raises, and the
    asymmetry is the point: an absent old_status is a corrupt row a 409
    describes accurately, while an empty new_status is this module being
    called wrongly, which no client can fix and no status code should dress up
    as a conflict.
    """
    item = item_named(db_session, "SA100")
    before = db_session.query(models.EquipmentStatusHistory).count()

    with pytest.raises(ValueError, match="non-empty new_status"):
        audit_trail.set_status(
            db_session,
            equipment=item,
            actor=mock_matrix_db["master"],
            new_status=empty,
            reason=ChangeReason.VERIFICATION,
        )

    assert db_session.query(models.EquipmentStatusHistory).count() == before
    assert item.status == "Functional", "the status moved despite the refusal"


def test_record_event_refuses_a_location_and_a_recipient_together(
    db_session, mock_matrix_db
):
    """One column, one meaning.

    location is a place and recipient is a person, and transaction_logs has a
    single string to hold whichever it is. Accepting both would silently drop
    one -- and which one it dropped would be an implementation detail of the
    order two lines happen to run in.
    """
    item = item_named(db_session, "SA100")

    with pytest.raises(ValueError, match="not both"):
        audit_trail.record_event(
            db_session,
            equipment=item,
            actor=mock_matrix_db["master"],
            event_type=EventType.ASSIGN,
            location="Armory",
            recipient=mock_matrix_db["soldier_a"],
        )


# --- 7. Fault and repair (DATA-H4-2) -----------------------------------------
#
# Both routes moved onto the writer here. Before this sub-task report_fault set
# "Malfunctioning" and wrote nothing at all, and fix_equipment set "Functional"
# and wrote only the movement log -- so an item could be broken and repaired for
# years while GET /equipment/{id}/history stayed empty. These tests are about
# the two tables together: neither route is correct if only one of them fills.


def report_fault(
    client, who, equipment_id, description="found on parade", fault="Cracked Housing"
):
    return client.post(
        "/maintenance/report",
        json={
            "equipment_id": equipment_id,
            "fault_name": fault,
            "description": description,
        },
        headers=create_auth_header(who),
    )


def fix_fault(client, who, equipment_id):
    return client.post(
        f"/maintenance/fix/{equipment_id}", headers=create_auth_header(who)
    )


def history_for(db_session, item):
    return (
        db_session.query(models.EquipmentStatusHistory)
        .filter_by(equipment_id=item.id)
        .order_by(models.EquipmentStatusHistory.id)
        .all()
    )


def logs_for(db_session, item, event_type):
    return (
        db_session.query(models.TransactionLog)
        .filter_by(equipment_id=item.id, event_type=event_type.value)
        .all()
    )


def test_reporting_a_fault_records_the_transition_and_the_event(
    client, db_session, mock_matrix_db
):
    """The headline: a fault report wrote neither table, and now writes both.

    They answer different questions and that is why both are asserted here. The
    history row is the item's CONDITION over time -- what it was, what it became,
    and why -- which is what an investigation into an unserviceable weapon reads.
    The log row is the movement report's record that somebody did something to
    this item today. A route filling one and not the other looks audited from
    whichever screen you happen to open.

    notes carries the reporter's description because the row otherwise says a
    fault moved the status and cannot say what the fault was, when the answer
    arrived in the same request.
    """
    item = item_named(db_session, "SA100")
    assert item.status == "Functional"

    res = report_fault(client, "u_soldier_a", item.id, description="stock split")
    assert res.status_code == 200, res.text

    db_session.refresh(item)
    assert item.status == "Malfunctioning"

    rows = history_for(db_session, item)
    assert len(rows) == 1, f"expected one history row, got {rows}"
    assert rows[0].old_status == "Functional"
    assert rows[0].new_status == "Malfunctioning"
    assert rows[0].change_reason == ChangeReason.FAULT_REPORT.value
    assert rows[0].notes == "stock split"
    assert rows[0].created_by == mock_matrix_db["soldier_a"].id
    assert rows[0].verification_id is None, (
        "a fault report is not a verification; that column belongs to "
        "create_verification and linking it here would fabricate a source"
    )

    assert len(logs_for(db_session, item, EventType.FAULT)) == 1


def test_an_empty_description_is_stored_as_nothing_rather_than_as_emptiness(
    client, db_session, mock_matrix_db
):
    """One column, two writers, one spelling for "nothing was said".

    schemas.ReportFaultRequest declares description as a bare `str` with no
    min_length, so "" is a legal body and a reporter who tabs past the field
    sends one. fix_equipment already writes `notes or None` for the same reason;
    two routes filling one column should not disagree about how to spell an
    absent note, or a reader has to know which route wrote the row before they
    can tell "said nothing" from "said nothing in particular".

    Caught by mutation rather than foresight: the `or None` was added here in
    review and nothing failed when it was taken out again.
    """
    item = item_named(db_session, "SA100")

    assert report_fault(client, "u_soldier_a", item.id, description="").status_code == 200

    rows = history_for(db_session, item)
    assert len(rows) == 1
    assert rows[0].notes is None, f"empty description stored as {rows[0].notes!r}"


def test_a_repeat_fault_is_an_event_and_not_a_transition(
    client, db_session, mock_matrix_db
):
    """The ruling set_status's docstring states, made executable.

    report_fault sets "Malfunctioning" unconditionally, so a second report on an
    already-broken item moves nothing. Writing a history row anyway would record
    Malfunctioning -> Malfunctioning: a transition that did not happen, in the
    one table whose columns are old_status and new_status.

    But somebody DID report something, and refusing to record that would be the
    opposite error. The log row is unconditional and the history row is not, and
    the two tables are complete together rather than separately. Kill either half
    of that asymmetry -- make the log conditional, or the history unconditional
    -- and exactly this test fails.
    """
    item = item_named(db_session, "SA100")

    assert report_fault(client, "u_soldier_a", item.id).status_code == 200
    assert report_fault(client, "u_soldier_a", item.id, "again").status_code == 200

    db_session.refresh(item)
    assert item.status == "Malfunctioning"

    rows = history_for(db_session, item)
    assert len(rows) == 1, (
        f"the second report moved no status and must add no row, got {rows}"
    )
    assert rows[0].notes != "again", "the no-op must not overwrite the first row"

    assert len(logs_for(db_session, item, EventType.FAULT)) == 2, (
        "both reports happened; transaction_logs is where that is recorded"
    )


def test_repairing_records_the_transition_beside_the_log_it_already_wrote(
    client, db_session, mock_matrix_db
):
    """fix_equipment logged the FIX and wrote no history row. Now it does both.

    The pre-existing log is asserted too, and deliberately: cutting this route
    onto set_status touches the lines directly above record_event, and a
    regression that drops or duplicates the FIX row would otherwise be invisible
    to a test that only counted the new table.

    notes is NULL rather than "" -- `notes` is a query parameter no client sends
    (DATA-M5), so the empty string is the absence of a value and the column
    should say so.
    """
    item = item_named(db_session, "SA100")
    assert report_fault(client, "u_soldier_a", item.id).status_code == 200

    res = fix_fault(client, "u_bat_cmdr", item.id)
    assert res.status_code == 200, res.text

    db_session.refresh(item)
    assert item.status == "Functional"

    rows = history_for(db_session, item)
    assert len(rows) == 2, f"expected the fault row and the repair row, got {rows}"
    assert rows[1].old_status == "Malfunctioning"
    assert rows[1].new_status == "Functional"
    assert rows[1].change_reason == ChangeReason.REPAIR.value
    assert rows[1].notes is None
    assert rows[1].created_by == mock_matrix_db["bat_cmdr"].id

    assert len(logs_for(db_session, item, EventType.FIX)) == 1


def test_fixing_an_unbroken_item_still_closes_its_tickets(
    client, db_session, mock_matrix_db
):
    """The no-op rule must decline a row, not swallow the rest of the route.

    set_status returns early when the status does not move, and it is called
    ABOVE the ticket-closing update and the FIX log. An early return that had
    been written as a raise, or a caller that had guarded the remaining work
    behind it, would leave open tickets on an item everyone can see is
    Functional -- the readiness report and the ticket list disagreeing, with
    nothing in either to explain why.

    Reachable in practice: two techs closing the same ticket, or a fix on an
    item whose fault was never reported through this route.
    """
    item = item_named(db_session, "SA100")
    assert report_fault(client, "u_cmdr_a", item.id).status_code == 200
    assert fix_fault(client, "u_bat_cmdr", item.id).status_code == 200

    before = len(history_for(db_session, item))

    res = fix_fault(client, "u_bat_cmdr", item.id)
    assert res.status_code == 200, res.text

    db_session.refresh(item)
    assert item.status == "Functional"
    assert len(history_for(db_session, item)) == before, (
        "nothing moved, so no transition was recorded"
    )
    assert len(logs_for(db_session, item, EventType.FIX)) == 2, (
        "the FIX log is unconditional -- a tech acted on this item twice"
    )
    open_tickets = (
        db_session.query(models.MaintenanceLog)
        .filter(
            models.MaintenanceLog.equipment_id == item.id,
            models.MaintenanceLog.status != "Closed",
        )
        .count()
    )
    assert open_tickets == 0


def test_a_refused_fault_report_writes_neither_table(
    client, db_session, mock_matrix_db
):
    """The gates are above both writes, extended to the rows H4-2 adds.

    tests/test_status_authority.py already pins that a refused write leaves no
    FaultType and no MaintenanceLog behind. These two tables are new to these
    routes and inherit nothing from that; a history row for a refused report is
    an audit trail asserting a state change that was denied, which is worse than
    no audit trail because it is believed.

    Company A's commander against Company B's item: a 404 from the resolver,
    which is the gate that fires before require_status_authority is reached.
    """
    item = item_named(db_session, "SB200")
    history_before = db_session.query(models.EquipmentStatusHistory).count()
    logs_before = db_session.query(models.TransactionLog).count()

    assert report_fault(client, "u_cmdr_a", item.id).status_code == 404
    assert fix_fault(client, "u_cmdr_a", item.id).status_code == 404

    assert db_session.query(models.EquipmentStatusHistory).count() == history_before
    assert db_session.query(models.TransactionLog).count() == logs_before


def test_a_soldier_refused_the_fix_leaves_the_repair_unrecorded(
    client, db_session, mock_matrix_db
):
    """The other refusal shape: seen, held, and still not permitted.

    The 404 above never reaches the verb. Here soldier_a HOLDS SA100, so the
    resolver returns it and authz.require is what refuses -- the deeper of the
    two gates, and the one a set_status call placed a line too high would sail
    past, writing the transition and then 403-ing. The status is asserted
    unchanged for the same reason.
    """
    item = item_named(db_session, "SA100")
    assert report_fault(client, "u_soldier_a", item.id).status_code == 200
    before = len(history_for(db_session, item))

    assert fix_fault(client, "u_soldier_a", item.id).status_code == 403

    db_session.refresh(item)
    assert item.status == "Malfunctioning"
    assert len(history_for(db_session, item)) == before


def test_the_audit_write_dies_with_the_ticket_it_explains(
    client, db_session, mock_matrix_db, monkeypatch
):
    """Why both writes sit BELOW the find-or-create commit, and in its flush.

    report_fault commits mid-route to mint a novel FaultType. Audit above that
    commit and a later failure leaves a COMMITTED history row explaining a
    ticket that was rolled back -- DATA-M4's shape at a new site, and the worst
    kind of audit defect: a permanent record of something that did not happen.

    THE FAILURE IS FORCED AT THE FINAL COMMIT, and where it is forced is the
    whole design of this test. An earlier version exploded the MaintenanceLog
    CONSTRUCTOR, which fires two lines above the audit calls -- so under correct
    code neither call ever ran, and the empty table proved only that unreached
    code writes nothing. Failing on the second commit means record_event and
    set_status have run and staged their rows, exactly as in production, before
    anything goes wrong. The fault name is deliberately novel so that a first
    commit exists to be counted; with a seeded name there is no mid-route commit
    at all, which the assertion below checks rather than assumes.

    ONE reachable defect, and this is it. A review asked for a stray
    `db.commit()` between the audit writes and the route's own to be caught too;
    it is not, and it should not be. `db.add(log)` runs ABOVE both audit calls,
    so any commit that could persist an audit row persists the ticket in the
    same transaction -- the rows cannot be separated by adding a commit, only by
    hoisting the audit writes above the fault-type commit that already exists.
    Mutation-tested both ways: hoisting fails this test, a stray commit does not
    and describes no defect. A test written to "catch" it would be asserting
    something untrue about the session.

    Both tables are asserted. The history row was the obvious one and the
    TransactionLog row is the one an earlier version missed entirely -- a FAULT
    event surviving a rolled-back ticket is the same defect wearing the other
    table's clothes.
    """
    item = item_named(db_session, "SA100")
    logs_before = db_session.query(models.TransactionLog).count()

    class Exploding(Exception):
        pass

    real_commit = db_session.commit
    calls = {"n": 0}

    def failing_commit():
        calls["n"] += 1
        # 1 = the find-or-create commit that mints the FaultType. 2 = the
        # route's own, the one carrying the ticket and both audit rows.
        if calls["n"] >= 2:
            raise Exploding("ticket commit failed")
        return real_commit()

    monkeypatch.setattr(db_session, "commit", failing_commit)

    with pytest.raises(Exploding):
        report_fault(
            client,
            "u_soldier_a",
            item.id,
            description="never lands",
            fault="Novel Fault For This Test",
        )

    assert calls["n"] >= 2, (
        "the mid-route commit never happened, so this test proved nothing -- "
        "the fault name must be one no fixture seeds"
    )

    monkeypatch.undo()
    db_session.rollback()

    assert history_for(db_session, item) == [], (
        "a history row survived a failed ticket; the audit writes must sit "
        "below the fault-type commit and share the ticket's flush"
    )
    assert db_session.query(models.TransactionLog).count() == logs_before, (
        "a FAULT log survived a failed ticket"
    )
    db_session.refresh(item)
    assert item.status == "Functional"


def test_a_fault_report_on_a_statusless_item_is_refused(
    client, db_session, mock_matrix_db
):
    """H4-1 409 ruling, through the route H4-2 newly exposes it on.

    That refusal was pinned through /verifications/ only, because that was the
    one caller. report_fault is now a second, and it is the one where the old
    behaviour was worst: the bare assignment overwrote a NULL status silently,
    so a corrupt row got quietly repaired into "Malfunctioning" with no record
    that it had ever been broken.

    Written by raw SQL because nothing in the application can produce this state
    -- the same construction tests/test_sensitivity_contract.py uses for a NULL
    sensitivity.
    """
    item = item_named(db_session, "SA100")
    db_session.execute(
        text("UPDATE equipment SET status = NULL WHERE id = :id"), {"id": item.id}
    )
    db_session.commit()
    db_session.expire_all()

    res = report_fault(client, "u_soldier_a", item.id)
    assert res.status_code == 409, res.text

    assert history_for(db_session, item) == []


# --- 8. Reclassification, creation and condition reports (DATA-H4-3) ---------


def classify(client, who, equipment_id, sensitivity="CLASSIFIED"):
    return client.patch(
        f"/equipment/{equipment_id}/sensitivity",
        json={"sensitivity": sensitivity},
        headers=create_auth_header(who),
    )


def create_item(client, who, serial, **extra):
    return client.post(
        "/equipment/",
        json={"catalog_name": "Rifle", "serial_number": serial, **extra},
        headers=create_auth_header(who),
    )


def movement_report(client, who):
    res = client.get("/reports/daily_movement", headers=create_auth_header(who))
    assert res.status_code == 200, res.text
    return res.json()


def test_classifying_an_item_records_who_decided_it(
    client, db_session, mock_matrix_db
):
    """The event DATA-H3-2 created and left unrecorded for a whole ticket.

    location IS NULL is asserted, not incidental. It is the one column that
    could have carried the new value, and the ruling in EventType.RECLASSIFY is
    that it must not -- it already means a place and a person, and a reader
    facing a third meaning cannot tell which one a row carries. A later author
    who "improves" the row by writing "CLASSIFIED" there fails here.
    """
    item = item_named(db_session, "SA100")
    assert item.sensitivity == "UNCLASSIFIED", "fixture precondition"

    res = classify(client, "u_master", item.id)
    assert res.status_code == 200, res.text

    db_session.expire_all()
    assert item_named(db_session, "SA100").sensitivity == "CLASSIFIED"

    logs = logs_for(db_session, item, EventType.RECLASSIFY)
    assert len(logs) == 1, "classifying wrote no RECLASSIFY row"
    assert logs[0].involved_user_id == mock_matrix_db["master"].id
    assert logs[0].location is None, (
        "the RECLASSIFY row put something in `location`, a column that already "
        "means a place and a person -- see EventType.RECLASSIFY"
    )


def test_reasserting_a_classification_is_still_an_event(
    client, db_session, mock_matrix_db
):
    """The transaction_logs/history asymmetry, made executable on a new column.

    set_status returns early when nothing moved, because equipment_status_history
    records TRANSITIONS. set_sensitivity deliberately does not, because
    transaction_logs records EVENTS -- and PATCHing CLASSIFIED onto an
    already-classified item is a person making a classification decision, even
    though the column does not change.

    The mirror of test_a_repeat_fault_is_an_event_and_not_a_transition above,
    which is where this rule was settled. Copying the no-op guard from
    set_status into set_sensitivity is the plausible mistake, and it fails here.
    """
    item = item_named(db_session, "SA100")

    assert classify(client, "u_master", item.id).status_code == 200
    assert classify(client, "u_master", item.id).status_code == 200

    assert len(logs_for(db_session, item, EventType.RECLASSIFY)) == 2, (
        "the second classification of an already-classified item wrote no row; "
        "transaction_logs records events, not transitions"
    )


def test_a_refused_reclassification_records_nothing(
    client, db_session, mock_matrix_db
):
    """The gate is above the writer, so a 403 leaves neither a row nor a change.

    u_cmdr_a is the fixture's holder-without-the-verb: they hold VIEW over
    188/53/A, so they get a 403 rather than a 404, and they do NOT hold
    SET_SENSITIVITY -- the deliberate narrowing conftest.py documents at the
    grant table. That is what makes this a test of ordering rather than of
    visibility.
    """
    item = item_named(db_session, "SA100")
    before = db_session.query(models.TransactionLog).count()

    res = classify(client, "u_cmdr_a", item.id)
    assert res.status_code == 403, res.text

    db_session.expire_all()
    assert item_named(db_session, "SA100").sensitivity == "UNCLASSIFIED"
    assert db_session.query(models.TransactionLog).count() == before


def test_the_reclassify_row_is_hidden_by_the_act_it_records(
    client, db_session, mock_matrix_db
):
    """Sensitivity's third named limit, executable.

    scope_equipment_derived_query filters on the item's CURRENT sensitivity, so
    classifying an item retroactively removes every transaction_logs row about
    it -- including the row that records the classification. The caller who
    would most want to know why the item vanished is exactly the caller who
    cannot see the answer.

    Asserted from both ends in one test on purpose. The cleared half is what
    makes the uncleared half mean something: without it, an assertion that
    u_cmdr_a cannot see the row is satisfied by the row not existing, which is
    the failure this whole ticket is about.

    Not a defect to fix here. It is the honest consequence of scoping audit rows
    through the item they describe, which is SEC-H5's fix; the alternative --
    logs outliving their subject's visibility -- is worse. Named in
    backend/enums.py Sensitivity rather than left for someone to discover.
    """
    item = item_named(db_session, "SA100")

    assert classify(client, "u_master", item.id).status_code == 200

    def reclassify_rows(who):
        return [
            row for row in movement_report(client, who)
            if row["event_type"] == "RECLASSIFY" and row["serial_number"] == "SA100"
        ]

    assert len(reclassify_rows("u_master")) == 1, (
        "the classifier cannot see the row they just wrote -- this test can "
        "prove nothing about the uncleared caller from that starting state"
    )
    assert reclassify_rows("u_cmdr_a") == [], (
        "an uncleared caller can see the RECLASSIFY row for a classified item; "
        "scope_equipment_derived_query is not inheriting the clause"
    )


def test_a_created_item_is_born_with_a_log_that_reaches_the_report(
    client, db_session, mock_matrix_db
):
    """Equipment has no created_at column, so this row is the only birth record.

    ASSERTED THROUGH THE REPORT, and that is the whole design of this test.
    record_event reads equipment.id, which is None until the INSERT runs, so
    dropping create_equipment's db.flush() writes a TransactionLog with a NULL
    equipment_id -- which scope_equipment_derived_query's inner join discards
    silently. Nothing raises. The row exists. A count of transaction_logs still
    goes up by one. Only asking the endpoint tells the two apart.
    """
    res = create_item(client, "u_master", "NEW-001")
    assert res.status_code == 200, res.text
    new_id = res.json()["id"]

    logs = logs_for(db_session, item_named(db_session, "NEW-001"), EventType.CREATE)
    assert len(logs) == 1, "the creation wrote no CREATE row"
    assert logs[0].involved_user_id == mock_matrix_db["master"].id
    assert logs[0].equipment_id == new_id, (
        "the CREATE row carries no equipment_id -- record_event read a pending "
        "item's id before it was flushed"
    )

    rows = [
        row for row in movement_report(client, "u_master")
        if row["event_type"] == "CREATE" and row["serial_number"] == "NEW-001"
    ]
    assert len(rows) == 1, (
        "the CREATE row never reached the movement report -- if the row exists "
        "in the table, its equipment_id is NULL and the inner join dropped it, "
        "which is what create_equipment's db.flush() prevents"
    )


def test_a_refused_creation_leaves_neither_an_item_nor_a_log(
    client, db_session, mock_matrix_db
):
    """The gate is two blocks above the writer, with a commit in between.

    u_soldier_a holds no CREATE_EQUIPMENT anywhere. The refusal happens before
    the CatalogItem find-or-create commits, so this also pins the ordering that
    keeps a denied request from leaving a permanent catalog row behind under an
    attacker-chosen name.
    """
    before = db_session.query(models.TransactionLog).count()

    res = create_item(client, "u_soldier_a", "NEW-002")
    assert res.status_code == 403, res.text

    assert db_session.query(models.TransactionLog).count() == before
    assert db_session.query(models.Equipment).filter_by(
        serial_number="NEW-002"
    ).count() == 0


def test_record_event_refuses_an_equipment_that_has_no_id_yet(
    db_session, mock_matrix_db
):
    """The one failure in this module that no assertion could otherwise see.

    A pending equipment has id None, so the row would carry a NULL
    equipment_id -- and scope_equipment_derived_query joins INNER, so it is
    dropped from the movement report and every listing built on it. Nothing
    raises, the row exists, and a count of transaction_logs still goes up by
    one. Written, committed, visible to nobody.

    create_equipment reached exactly this by construction: it is the only route
    that audits an item it has just built. Its db.flush() is what avoids it,
    and before this check deleting that line was SILENT -- the reason the
    end-to-end test for it has to assert through /reports/daily_movement rather
    than by counting rows.

    A refusal rather than a flush here, because the writer-never-commits guard
    above forbids this module owning a transaction operation, and H4-1's ruling
    is worth more than one route's convenience. The caller keeps the flush and
    loses the silence.
    """
    pending = models.Equipment(
        catalog_item_id=item_named(db_session, "SA100").catalog_item_id,
        serial_number="PENDING-001",
        group_id=item_named(db_session, "SA100").group_id,
    )
    db_session.add(pending)
    assert pending.id is None, "fixture precondition: not flushed"

    before = db_session.query(models.TransactionLog).count()

    with pytest.raises(ValueError, match="persisted equipment"):
        audit_trail.record_event(
            db_session,
            equipment=pending,
            actor=mock_matrix_db["master"],
            event_type=EventType.CREATE,
        )

    assert db_session.query(models.TransactionLog).count() == before, (
        "the unreachable row was written before the refusal"
    )


@pytest.mark.parametrize("empty", [None, ""], ids=["none", "empty_string"])
def test_set_sensitivity_refuses_an_empty_new_sensitivity(
    db_session, mock_matrix_db, empty
):
    """The sibling of set_status's guard, which DATA-H4-3 shipped without.

    Found by mutation: deleting the ValueError left the whole suite green. The
    guard was written as a mirror of set_status's and the mirroring stopped at
    the code, which is the failure mode a one-caller helper invites -- the one
    caller passes a validated Sensitivity member, so nothing exercises the
    branch until the second caller arrives and does not.

    What an unguarded empty value costs here is different from its status
    counterpart, and worse in one specific way. An empty new_status writes a
    history row claiming a transition INTO nothing, which is visible in the
    table. An empty sensitivity DECLASSIFIES the item. models.py's
    column default applies at INSERT, not on update, so both spellings survive
    the write -- "" as itself, None as NULL -- and scope_equipment_query
    matches with is_distinct_from, under which each is TRUE against
    CLASSIFIED. The item becomes visible to callers holding no
    VIEW_CLASSIFIED: a silent widening of who can see it, recorded as a
    RECLASSIFY row that -- by this module's own design -- does not say what it
    changed to.

    Both halves are asserted for that reason: the raise, and the column not
    having moved before it.
    """
    item = item_named(db_session, "SA100")
    before = db_session.query(models.TransactionLog).count()

    with pytest.raises(ValueError, match="non-empty new_sensitivity"):
        audit_trail.set_sensitivity(
            db_session,
            equipment=item,
            actor=mock_matrix_db["master"],
            new_sensitivity=empty,
        )

    assert item.sensitivity == "UNCLASSIFIED", (
        "the classification moved despite the refusal"
    )
    assert db_session.query(models.TransactionLog).count() == before


def test_the_creation_log_dies_with_the_item_it_records(
    client, db_session, mock_matrix_db, monkeypatch
):
    """A flushed row is not a committed one, and this route commits twice.

    The hazard is specific and the code invites it: create_equipment already
    calls db.commit() for the CatalogItem, so an author reading top-to-bottom
    can reasonably place the audit write above the Equipment insert and believe
    it is covered. It would then survive a failure of the real commit.

    Forced by failing the commit that would PERSIST the audit row, identified
    by looking in the session rather than by counting calls. An earlier version
    failed the second commit, which worked only because "Rifle" is absent from
    the catalog fixture and the find-or-create therefore commits first. Give
    this route an existing catalog name and there is exactly one commit, the
    mutation never fires, and the test reports "DID NOT RAISE" -- reading as
    though the audit row survived when in fact nothing was tested. Verified by
    doing it. The trigger now names the condition the test is about.
    """
    real_commit = Session.commit

    def failing_commit(self):
        if any(isinstance(obj, models.TransactionLog) for obj in self.new):
            raise RuntimeError("commit failed after the audit write")
        return real_commit(self)

    monkeypatch.setattr(Session, "commit", failing_commit)

    with pytest.raises(RuntimeError):
        create_item(client, "u_master", "NEW-003")

    monkeypatch.undo()
    db_session.rollback()

    assert db_session.query(models.Equipment).filter_by(
        serial_number="NEW-003"
    ).count() == 0
    assert db_session.query(models.TransactionLog).filter_by(
        event_type="CREATE"
    ).count() == 0, (
        "a CREATE row outlived the item it records, so the movement report "
        "carries the birth of an item that does not exist"
    )


def test_a_condition_report_is_not_the_daily_verification(
    client, db_session, mock_matrix_db
):
    """Two acts, two gates, two EventType values -- asserted in both directions.

    verify_equipment_daily is gated on possession alone and means "the item is
    where I say it is". create_verification is gated on require_status_authority
    and means "here is what condition it is in". EventType.VERIFICATION's
    comment has claimed the distinction since DATA-H4-1; until DATA-H4-3 the
    second act emitted nothing at all, so the claim was untested.

    The negative assertions are the load-bearing half. Reusing VERIFICATION for
    the condition report is the obvious shortcut, and it passes any test that
    only checks a row exists.
    """
    item = item_named(db_session, "SA100")
    holder = db_session.query(models.User).filter_by(
        id=item.holder_user_id
    ).one().personal_number

    res = client.post(
        "/verifications/",
        json={
            "equipment_id": item.id,
            "verification_type": "daily",
            "reported_status": "Functional",
            "findings": "no change",
            "action_required": False,
        },
        headers=create_auth_header(holder),
    )
    assert res.status_code == 200, res.text

    assert len(logs_for(db_session, item, EventType.CONDITION_REPORT)) == 1
    assert logs_for(db_session, item, EventType.VERIFICATION) == [], (
        "the condition report emitted VERIFICATION, which means the daily "
        "presence check -- see EventType.VERIFICATION"
    )

    res = client.post(
        f"/equipment/{item.id}/verify", headers=create_auth_header(holder)
    )
    assert res.status_code == 200, res.text

    assert len(logs_for(db_session, item, EventType.VERIFICATION)) == 1
    assert len(logs_for(db_session, item, EventType.CONDITION_REPORT)) == 1, (
        "the daily presence check emitted CONDITION_REPORT"
    )


def test_a_refused_condition_report_writes_neither_table(
    client, db_session, mock_matrix_db
):
    """The unconditional write is still below the gate.

    An unconditional row is the one most likely to be hoisted above a gate,
    because it reads as bookkeeping rather than as part of the change. u_soldier_b
    neither holds SA100 nor carries REPORT_STATUS over Company A, so
    require_status_authority refuses on both arms.
    """
    item = item_named(db_session, "SA100")
    logs_before = db_session.query(models.TransactionLog).count()

    res = client.post(
        "/verifications/",
        json={
            "equipment_id": item.id,
            "verification_type": "daily",
            "reported_status": "Malfunctioning",
            "findings": "should not be recorded",
            "action_required": True,
        },
        headers=create_auth_header("u_soldier_b"),
    )
    assert res.status_code in (403, 404), res.text

    assert db_session.query(models.TransactionLog).count() == logs_before
    assert history_for(db_session, item) == []
