"""DATA-H4-1: one writer for the audit tables, and nothing that can go round it.

backend/audit_trail.py's docstring carries the census of what was wrong; this
file does not repeat it, so the two cannot drift as DATA-H4-2 and -3 change
the counts.

The ticket's fix is "route every state mutation through one audit-writing
helper so no path CAN bypass it". The first two tests here are that sentence.
Everything below them is the behaviour the helper is supposed to have.

WHY THE GUARDS ARE AST WALKS AND NOT REVIEW
--------------------------------------------
The four divergent call sites WERE a convention, followed by every author who
touched them and wrong anyway. A fifth site added next month is refused by a
failing test or by nothing; there is no third option, and tests/test_utc_contract
.py:31-64 already makes exactly this argument for datetime.utcnow() ("the ONLY
defence against partial application"). This file copies its shape deliberately.
"""
import ast
import re
from pathlib import Path

import pytest
from sqlalchemy import text

import backend
from backend import audit_trail, models
from backend.enums import ChangeReason, EventType
from tests.conftest import create_auth_header

BACKEND_ROOT = Path(backend.__file__).parent
WRITER = "audit_trail.py"

AUDIT_TABLES = ("TransactionLog", "EquipmentStatusHistory")

# The two sites that still move a status directly are marked at the line that
# does it, with this pragma and the sub-task that closes them:
#
#     item.status = "Malfunctioning"  # audit-trail-bypass: DATA-H4-2
#
# A third is permanent: fix_equipment's ticket-closing bulk update, where a
# ticket's Open/Closed is not an equipment status and equipment_status_history
# has no row shape for it.
#
# A pragma rather than a (file, function) tuple listed here, and the difference
# is not cosmetic. A function-keyed waiver exempts the whole FUNCTION: an
# author cutting report_fault onto the helper who left one stray assignment
# behind would keep it hidden, and a staleness check comparing sets could not
# see it either. It also breaks silently on rename, and worse, silently
# TRANSFERS to any new function that reuses the name. The pragma travels with
# the statement, is visible where the bypass is, and cannot drift.
BYPASS_PRAGMA = "audit-trail-bypass:"


def backend_modules():
    """Every source file under backend/, as (relative posix path, parsed tree)."""
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        yield (
            path.relative_to(BACKEND_ROOT).as_posix(),
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path)),
        )


def _names_status(key):
    """Is this dict key the `status` column, in either spelling?

    `{"status": x}` is an ast.Constant; `{models.Equipment.status: x}` is an
    ast.Attribute and is the more idiomatic SQLAlchemy of the two. Matching
    only the string was a hole a reviewer walked straight through.
    """
    if isinstance(key, ast.Constant):
        return key.value == "status"
    return isinstance(key, ast.Attribute) and key.attr == "status"


def status_assignments(tree):
    """Every statement that moves an equipment status, as AST nodes.

    TWO SHAPES, because Python and SQLAlchemy each offer one.

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
            and node.attr == "status"
            and isinstance(node.ctx, ast.Store)
        ):
            yield node
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
        ):
            for argument in node.args:
                if isinstance(argument, ast.Dict) and any(_names_status(key)
                                                          for key in argument.keys):
                    yield node


def enclosing_function(tree, target):
    """The name of the def containing `target`, for a legible failure message."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            child is target for child in ast.walk(node)
        ):
            return node.name
    return "<module>"


# --- 1. The two guards ------------------------------------------------------


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


def test_only_audit_trail_assigns_equipment_status():
    """`something.status = x` happens in one file, plus a waiver DATA-H4-2 empties.

    The construction guard above is only half the claim. A route that calls
    record_event and then assigns .status itself has produced a movement-log
    entry and no status history -- which is precisely what fix_equipment did
    before this ticket, so the failure mode is attested rather than imagined.

    Constructor kwargs are silent here: seed_data.py's `status="Functional"` is
    an ast.keyword on a Call, it creates a row rather than transitions one, and
    there is no prior status for a history row to record.

    An intended bypass marks its own line with the pragma above. The three that
    exist are in maintenance.py: two equipment writes that close with
    DATA-H4-2, and the ticket-closing bulk update, which is permanent.

    The match is on the NAME `status`, with no idea whose. An unrelated class
    doing `self.status = "ok"` under backend/ would be flagged, and the right
    repair then is to narrow this guard by target -- not to pragma correct
    code, which is how a guard becomes noise and then becomes deleted.
    """
    offenders = []

    for relpath, tree in backend_modules():
        if relpath == WRITER:
            continue
        lines = (BACKEND_ROOT / relpath).read_text(encoding="utf-8").splitlines()
        for node in status_assignments(tree):
            if BYPASS_PRAGMA in lines[node.lineno - 1]:
                continue
            offenders.append(
                f"{relpath}:{node.lineno} in {enclosing_function(tree, node)}()"
            )

    assert offenders == [], (
        "equipment status is assigned outside audit_trail.set_status, so a "
        f"status can move with no history row explaining it: {offenders}"
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


DASHBOARD_TABLE = (
    BACKEND_ROOT.parent
    / "frontend/src/features/dashboard/components/DailyActivityTable.tsx"
)


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
        path.read_text(encoding="utf-8")
        for path in BACKEND_ROOT.rglob("*.py")
        if path.name != "enums.py"
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

    Checked as a substring rather than by parsing TSX: the assertion is that a
    key exists, and a regex pretending to be a parser would be the more fragile
    of the two.
    """
    assert DASHBOARD_TABLE.exists(), f"dashboard table moved: {DASHBOARD_TABLE}"
    source = DASHBOARD_TABLE.read_text(encoding="utf-8")
    table = source.split("const EVENT_META")[1].split("};")[0]

    missing = [e.value for e in EventType if f"{e.value.lower()}: {{" not in table]

    assert missing == [], (
        f"event types the dashboard cannot label: {missing}. They render as raw "
        "English in a Hebrew RTL table; add the arm in the commit that emits them."
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

