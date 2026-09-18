"""DATA-H7: deleting a fault type a ticket still references is refused, not crashed.

`DELETE /setup/fault_types/{id}` checked authority and existence and then
deleted, while `maintenance_logs.fault_type_id` references the row. Any fault
type a ticket had ever used therefore failed at the database, and the failure
reached the caller as a 500 carrying the constraint's name -- a leak of the
schema in the same breath as an unusable error.

Two halves are asserted throughout and neither is sufficient alone:

  1. **The status code.** 409, and specifically NOT 500. A refusal the caller
     can act on, in place of a crash they cannot.

  2. **The body.** The constraint, the table and the column stay out of it.
     Reporting the leak was half of what the ticket said, so a fix that
     returned 409 and still named `maintenance_logs` would close one half and
     leave the other open. tests/test_audit_trail.py asserts the same absence
     for the 409 in audit_trail.set_status.

The refusal is raised from TWO places and they are tested separately: the
pre-check, and a backstop around the commit. The pre-check is check-then-act
and cannot be atomic, so a ticket filed inside that window would otherwise meet
the constraint raw -- the same defect through a narrower door. DATA-H13 does
not close that window either, since an ON DELETE rule changes which error the
database raises rather than whether it raises one.

WHAT THIS DOES NOT CLOSE, and it is worth a reader knowing where the edge is:
the constraint itself still has no ON DELETE rule, so a direct SQL delete
violates it exactly as before. DATA-H13 owns that for every foreign key in the
schema. This route is the only path DATA-H7 closes, which is why every test
here goes through the API rather than the session.

Tickets are created through POST /maintenance/report rather than by inserting
rows, so the association under test is the one the application actually makes
-- including the find-or-create in that route, which is what attaches a report
to an EXISTING fault type by name.
"""
import pytest
from sqlalchemy.exc import IntegrityError

from backend import models
from backend.database import Base
from backend.routers import setup
from tests.conftest import create_auth_header
from tests.test_global_authority import a_fault, delete_fault
from tests.test_status_authority import item_named, report

# u_brig_cmdr holds MANAGE_CATALOG and is not a master, so every permitted call
# below shows the verb being consulted rather than a rank -- the same choice
# test_global_authority makes for this route. u_bat_cmdr commands a battalion
# and holds five verbs, none of them this one.
CATALOG = "u_brig_cmdr"
NO_CATALOG = "u_bat_cmdr"

# Reporting a fault needs authority over the item's group, not over the
# vocabulary: a company commander has it for their own company's kit.
REPORTER = "u_cmdr_a"

# The words that must never reach a caller. The column and table are the
# schema; "SELECT"/"INSERT" and the constraint spelling are what a raw
# IntegrityError drags along with it.
LEAKS = ("maintenance_logs", "fault_type_id", "FOREIGN KEY", "constraint",
         "SELECT", "INSERT", "UPDATE", "sqlalchemy", "IntegrityError")


def ticket_count(db, fault_id):
    return db.query(models.MaintenanceLog).filter_by(fault_type_id=fault_id).count()


def fault_exists(db, fault_id):
    return db.query(models.FaultType).filter_by(id=fault_id).first() is not None


def counting_the_precheck(monkeypatch):
    """Count the counts, which is how a test tells the two refusal paths apart.

    Over HTTP the pre-check and the commit backstop answer identically, and
    that indistinguishability is asserted further down as a requirement. The
    cost is that the backstop COVERS FOR the pre-check: blind the pre-check,
    narrow it to open tickets only, or delete it outright, and every assertion
    about status codes and message text still passes because the constraint
    catches what the pre-check missed.

    So the remaining place to see which one answered is from inside. The
    pre-check path counts once; the backstop path counts again after its
    rollback, to report a number the pre-check never saw.
    """
    real = setup._tickets_holding
    calls = []

    def counted(db, fault_type_id):
        calls.append(fault_type_id)
        return real(db, fault_type_id)

    monkeypatch.setattr(setup, "_tickets_holding", counted)
    return calls


def assert_the_precheck_answered(calls):
    assert len(calls) == 1, (
        f"the count ran {len(calls)} times, so the commit backstop answered; "
        "the pre-check should have refused before any delete was attempted"
    )


def report_against(client, db, fault, serial="SA100", description="found on parade"):
    """File a real ticket that lands on `fault` rather than on a new type.

    report_fault finds-or-creates by NAME, so passing the existing name is what
    makes this a second reference to the same row instead of a second row.
    """
    item = item_named(db, serial)
    response = report(client, REPORTER, item.id, fault=fault.name, description=description)
    assert response.status_code == 200, response.text
    return response


# --- the refusal -----------------------------------------------------------


def test_deleting_a_used_fault_type_is_refused_rather_than_crashing(
    client, db_session, mock_matrix_db, monkeypatch
):
    """The defect itself: 409 where the route used to raise 500 at the database.

    Refused by the PRE-CHECK, asserted explicitly. One ticket is also the case
    that catches a pre-check written as "more than one" -- the backstop would
    otherwise answer 409 for it and hide the off-by-one entirely.
    """
    fault = a_fault(db_session, pending=False)
    report_against(client, db_session, fault)
    calls = counting_the_precheck(monkeypatch)

    response = delete_fault(client, CATALOG, fault.id)

    assert response.status_code == 409, response.text
    assert fault_exists(db_session, fault.id)
    assert_the_precheck_answered(calls)


def test_the_refused_delete_leaves_the_ticket_alone(client, db_session, mock_matrix_db):
    """Both rows survive, not merely the one the route was asked about.

    A "fix" that deleted the tickets to make room for the delete would satisfy
    the assertion above and destroy maintenance history to do it.
    """
    fault = a_fault(db_session, pending=False)
    report_against(client, db_session, fault)

    assert delete_fault(client, CATALOG, fault.id).status_code == 409

    assert ticket_count(db_session, fault.id) == 1


def test_the_refusal_names_the_fault_type_and_the_count(client, db_session, mock_matrix_db):
    """The message has to be actionable, which means saying what and how many.

    Naming the fault type discloses nothing: it is global vocabulary that every
    authenticated user can already enumerate through GET /setup/fault_types.
    """
    fault = a_fault(db_session, name="Cracked Housing", pending=False)
    report_against(client, db_session, fault)

    detail = delete_fault(client, CATALOG, fault.id).json()["detail"]

    assert "Cracked Housing" in detail
    assert "1" in detail


def test_the_refusal_leaks_no_schema(client, db_session, mock_matrix_db):
    """The other half of the ticket, asserted as an absence.

    A 409 that still spelled out the constraint would pass every assertion
    above and leave the disclosure exactly where it was.
    """
    fault = a_fault(db_session, pending=False)
    report_against(client, db_session, fault)

    body = delete_fault(client, CATALOG, fault.id).text

    for leak in LEAKS:
        assert leak.lower() not in body.lower(), f"{leak!r} reached the caller: {body}"


def test_the_count_is_the_number_of_tickets(client, db_session, mock_matrix_db):
    """Three reports, three in the message -- an off-by-one or a boolean is visible.

    Three DIFFERENT items, so this also pins that the count is over the fault
    type rather than over one item's history.
    """
    fault = a_fault(db_session, pending=False)
    for serial in ("SA100", "TA300", "SA100"):
        report_against(client, db_session, fault, serial=serial)

    detail = delete_fault(client, CATALOG, fault.id).json()["detail"]

    assert ticket_count(db_session, fault.id) == 3
    assert "3" in detail


# --- the shapes a narrower check would let through --------------------------


def test_a_closed_ticket_still_blocks_the_delete(
    client, db_session, mock_matrix_db, monkeypatch
):
    """"Only open tickets count" is the plausible wrong fix, and it still 500s.

    A closed ticket holds the reference exactly as an open one does; the
    constraint has no opinion about whether the work finished. Nothing else in
    this file distinguishes a status filter from no filter.

    The pre-check must be the one that refuses. A status filter here would let
    the delete proceed to the constraint, and the backstop would answer 409 --
    right code, wrong mechanism, and a defect invisible from outside.
    """
    fault = a_fault(db_session, pending=False)
    item = item_named(db_session, "SA100")
    report_against(client, db_session, fault)

    assert client.post(
        f"/maintenance/fix/{item.id}", headers=create_auth_header("u_tech_a")
    ).status_code == 200
    db_session.expire_all()
    assert db_session.query(models.MaintenanceLog).filter_by(
        fault_type_id=fault.id, status="Closed"
    ).count() == 1
    calls = counting_the_precheck(monkeypatch)

    assert delete_fault(client, CATALOG, fault.id).status_code == 409
    assert fault_exists(db_session, fault.id)
    assert_the_precheck_answered(calls)


def test_a_pending_fault_type_in_use_is_refused_too(
    client, db_session, mock_matrix_db, monkeypatch
):
    """Approval state is orthogonal to the reference, and easy to conflate.

    report_fault MINTS pending types for reporters who cannot skip review, so
    "pending" and "in use" arrive together constantly -- and a check that
    treated an unapproved type as disposable would crash on the commonest row
    in the table.
    """
    fault = a_fault(db_session, pending=True)
    report_against(client, db_session, fault)
    calls = counting_the_precheck(monkeypatch)

    assert delete_fault(client, CATALOG, fault.id).status_code == 409
    assert fault_exists(db_session, fault.id)
    assert_the_precheck_answered(calls)


def test_a_name_with_a_quote_is_reported_without_incident(
    client, db_session, mock_matrix_db
):
    """The name is interpolated into a MESSAGE, and must never be near SQL.

    An apostrophe is the character that tells the two apart: it comes back
    intact in the detail, and nothing downstream of the count sees it.
    """
    fault = a_fault(db_session, name="O'Brien's Mount", pending=False)
    report_against(client, db_session, fault)

    response = delete_fault(client, CATALOG, fault.id)

    assert response.status_code == 409, response.text
    assert "O'Brien's Mount" in response.json()["detail"]


# --- what must still work ---------------------------------------------------


def test_an_unused_fault_type_still_deletes(client, db_session, mock_matrix_db):
    """The permitted side. A pre-check that refused everything would pass above."""
    fault = a_fault(db_session, pending=False)

    assert delete_fault(client, CATALOG, fault.id).status_code == 200
    assert not fault_exists(db_session, fault.id)


def test_tickets_against_another_fault_type_do_not_block(
    client, db_session, mock_matrix_db
):
    """The count is filtered by fault type, not merely "are there any tickets".

    A check that counted the whole maintenance_logs table would refuse every
    delete on any database with a single ticket in it, and the test above --
    which has none -- would not notice.
    """
    used = a_fault(db_session, name="Cracked Housing", pending=False)
    unused = a_fault(db_session, name="Frayed Strap", pending=False)
    report_against(client, db_session, used)

    assert delete_fault(client, CATALOG, unused.id).status_code == 200
    assert not fault_exists(db_session, unused.id)
    assert fault_exists(db_session, used.id)


def test_authority_is_unchanged_for_a_fault_type_in_use(
    client, db_session, mock_matrix_db
):
    """403 before 409: the gate still runs first, and tells the refused nothing.

    Moving the new check above the gate would answer "in use" to a caller with
    no authority over the catalog at all -- a small oracle, and one this route
    never offered.
    """
    fault = a_fault(db_session, pending=False)
    report_against(client, db_session, fault)

    response = delete_fault(client, NO_CATALOG, fault.id)

    assert response.status_code == 403
    assert fault_exists(db_session, fault.id)
    assert ticket_count(db_session, fault.id) == 1


def test_the_unknown_id_is_still_a_404(client, db_session, mock_matrix_db):
    """The pre-check sits BELOW the existence check and does not shadow it."""
    assert delete_fault(client, CATALOG, 999999).status_code == 404


# --- the window the pre-check cannot cover ----------------------------------


def blind_the_precheck_once(monkeypatch):
    """Make the first count answer zero, and every later one tell the truth.

    This is the race, made deterministic: the pre-check looked, saw nothing,
    and a ticket arrived before the commit. Patching the count is the only way
    to reach the branch reliably -- a real concurrent insert would be timing
    dependent, and a test that passes only sometimes pins nothing.
    """
    real = setup._tickets_holding
    seen = []

    def counted(db, fault_type_id):
        seen.append(fault_type_id)
        return 0 if len(seen) == 1 else real(db, fault_type_id)

    monkeypatch.setattr(setup, "_tickets_holding", counted)
    return seen


def test_a_ticket_arriving_inside_the_race_window_is_still_refused_cleanly(
    client, db_session, mock_matrix_db, monkeypatch
):
    """409 from the commit backstop, not the raw IntegrityError the pre-check misses.

    Without the try/except this is a 500 carrying the constraint name -- the
    original defect, reached through the one door the pre-check cannot shut.
    """
    fault = a_fault(db_session, pending=False)
    report_against(client, db_session, fault)
    blind_the_precheck_once(monkeypatch)

    response = delete_fault(client, CATALOG, fault.id)

    assert response.status_code == 409, response.text
    assert fault_exists(db_session, fault.id)
    assert ticket_count(db_session, fault.id) == 1


def test_the_race_refusal_is_indistinguishable_from_the_pre_check_refusal(
    client, db_session, mock_matrix_db, monkeypatch
):
    """One message from both paths, so the caller learns nothing about timing.

    A second spelling of this refusal would drift from the first, and the drift
    would itself be a disclosure -- which branch answered is not the caller's
    business. The recount is what makes the counts agree.
    """
    fault = a_fault(db_session, name="Cracked Housing", pending=False)
    report_against(client, db_session, fault)
    expected = delete_fault(client, CATALOG, fault.id).json()["detail"]

    blind_the_precheck_once(monkeypatch)
    raced = delete_fault(client, CATALOG, fault.id).json()["detail"]

    assert raced == expected
    assert "Cracked Housing" in raced
    assert "1" in raced


def test_the_race_refusal_leaks_no_schema(
    client, db_session, mock_matrix_db, monkeypatch
):
    """The branch that has a live IntegrityError in hand must not pass it on.

    This is the path where the leak is easiest to reintroduce: the exception
    carrying the constraint name is right there, and re-raising or formatting
    it would satisfy every status-code assertion above.
    """
    fault = a_fault(db_session, pending=False)
    report_against(client, db_session, fault)
    blind_the_precheck_once(monkeypatch)

    body = delete_fault(client, CATALOG, fault.id).text

    for leak in LEAKS:
        assert leak.lower() not in body.lower(), f"{leak!r} reached the caller: {body}"


def test_the_session_survives_the_race_refusal(
    client, db_session, mock_matrix_db, monkeypatch
):
    """The rollback, asserted through a later write rather than by inspection.

    A failed flush left in the session poisons the NEXT request on it, which is
    how the original 500 spread beyond the request that caused it.
    """
    fault = a_fault(db_session, pending=False)
    spare = a_fault(db_session, name="Frayed Strap", pending=False)
    report_against(client, db_session, fault)
    blind_the_precheck_once(monkeypatch)

    assert delete_fault(client, CATALOG, fault.id).status_code == 409

    assert delete_fault(client, CATALOG, spare.id).status_code == 200
    assert not fault_exists(db_session, spare.id)


def test_an_unexplained_integrity_error_is_not_dressed_up_as_a_conflict(
    client, db_session, mock_matrix_db, monkeypatch
):
    """The backstop may only claim "tickets hold it" when tickets actually do.

    Simulated by blinding the count permanently while a real ticket exists, so
    the commit fails for a reason the route cannot account for -- which is what
    a future dependency writing earlier in the request, or a constraint added
    later, would look like from in here.

    The wrong behaviour is a tidy 409 reporting zero tickets: it reads as a
    handled case, explains nothing, and nobody investigates it. Raising is the
    honest answer even though a 500 is the uglier one.
    """
    fault = a_fault(db_session, pending=False)
    report_against(client, db_session, fault)
    monkeypatch.setattr(setup, "_tickets_holding", lambda db, fault_type_id: 0)

    with pytest.raises(IntegrityError):
        delete_fault(client, CATALOG, fault.id)

    db_session.rollback()
    assert fault_exists(db_session, fault.id)


def test_only_one_table_references_fault_types(db_session):
    """The assumption the commit backstop rests on, pinned rather than assumed.

    That branch catches IntegrityError WHOLE and reports it as "tickets hold
    this". Correct only while maintenance_logs is the sole referrer -- add a
    second one and the same catch starts giving the wrong reason for the
    refusal, naming a count that does not explain it.

    Failing here means going to delete_fault_type and deciding what the message
    should say now, not widening this test.
    """
    referrers = {
        f"{table.name}.{fk.parent.name}"
        for table in Base.metadata.sorted_tables
        for fk in table.foreign_keys
        if fk.column.table.name == "fault_types"
    }

    assert referrers == {"maintenance_logs.fault_type_id"}, (
        f"a new foreign key points at fault_types: {sorted(referrers)}; "
        "delete_fault_type's IntegrityError branch now reports the wrong reason"
    )


# --- the session the refusal leaves behind ----------------------------------


def test_the_session_still_works_after_a_refusal(client, db_session, mock_matrix_db):
    """A 409 raised before any write leaves nothing to roll back.

    The 500 this replaced left a failed flush in the session, so the NEXT
    request on it could fail for reasons of its own. Asserted with an ordinary
    write that has to commit.
    """
    fault = a_fault(db_session, pending=False)
    spare = a_fault(db_session, name="Frayed Strap", pending=False)
    report_against(client, db_session, fault)

    assert delete_fault(client, CATALOG, fault.id).status_code == 409

    assert delete_fault(client, CATALOG, spare.id).status_code == 200
    assert not fault_exists(db_session, spare.id)

    assert client.post(
        "/setup/fault_types?name=Bent Antenna", headers=create_auth_header(CATALOG)
    ).status_code == 200
    assert db_session.query(models.FaultType).filter_by(name="Bent Antenna").first() is not None
