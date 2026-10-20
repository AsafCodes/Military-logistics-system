"""DATA-M22-2: every take of a ticket is kept, released ones included.

DATA-M22-1 recorded only the current claim on the ticket, and a release
cleared it, so nobody could tell who had held a ticket. ticket_claims keeps
one row per take: start opens it, release ends it as Released, and a close
(close_ticket or fix_equipment) ends it as Closed. ended_by is whoever ended
it, who need not be whoever took it.

The ticket's own assigned_to_id and started_at stay as the current claim.
The routes write both sides in one transaction, and
test_the_ticket_and_its_claims_agree_after_every_route checks that they stay
in step.

The partial unique index (one open claim per ticket) and the two CHECKs are
asserted by raw SQL below, since no route can produce the rows they refuse.
ck_ticket_claims_outcome's vocabulary is also in test_status_vocabulary.py's
roll-call.
"""
import itertools
from datetime import datetime, timedelta, timezone

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend import clock, migrations, models
from backend.database import Base
from backend.enums import ClaimOutcome, TicketStatus
from tests.conftest import create_auth_header, recorded
from tests.test_group_schema import (
    _downgrade,
    _rerun_upgrade,
    _schema_snapshot,
    _upgrade,
)
from tests.test_ticket_claim import CONFLICTS, ROUTES, release, start
from tests.test_ticket_close import close, item_named, open_ticket, ticket, user_named
from tests.test_utc_migration_postgres import (
    POSTGRES_URL,
    WRONG_TIMEZONE,
    _migrate,
    pg_schema,  # noqa: F401  (a fixture)
)


@pytest.fixture
def ticking_clock(monkeypatch):
    """Every clock.utcnow() a millisecond after the one before.

    A claim's start is the ticket's started_at and its close end the ticket's
    closed_at because each pair comes from ONE reading. Windows' clock moves
    in 15.6 ms steps, so two readings in one request are usually equal there,
    and a claim time taken from a second reading would still match. With
    every reading distinct, it cannot."""
    start = clock.utcnow()
    ticks = itertools.count(1)
    monkeypatch.setattr(clock, "utcnow", lambda: start + timedelta(milliseconds=next(ticks)))


def claims(db, ticket_id):
    """The ticket's claims as rows, oldest first."""
    db.expire_all()
    return (
        db.query(models.TicketClaim)
        .filter_by(ticket_id=ticket_id)
        .order_by(models.TicketClaim.id)
        .all()
    )


def claim_rows(db):
    """Every claim as plain values, for before/after comparisons."""
    db.expire_all()
    return [
        (c.id, c.ticket_id, c.user_id, c.started_at, c.ended_at, c.ended_by_id, c.outcome)
        for c in db.query(models.TicketClaim).order_by(models.TicketClaim.id)
    ]


def fix(client, item_id, who="u_tech_a"):
    return client.post(f"/maintenance/fix/{item_id}", headers=create_auth_header(who))


def listed(client, who="u_master"):
    res = client.get("/tickets/", headers=create_auth_header(who))
    assert res.status_code == 200, res.text
    return {t["id"]: t for t in res.json()}


# --- what each route writes --------------------------------------------------


def test_start_opens_a_claim_at_the_ticket_s_started_at(
    client, db_session, mock_matrix_db, ticking_clock
):
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)

    assert start(client, tid).status_code == 200

    (claim,) = claims(db_session, tid)
    t = ticket(db_session, tid)
    assert claim.user_id == user_named(db_session, "u_tech_a").id == t.assigned_to_id
    assert claim.started_at == t.started_at
    assert (claim.ended_at, claim.ended_by_id, claim.outcome) == (None, None, None)


@pytest.mark.parametrize("releaser", ["u_tech_a", "u_master"], ids=["by_the_taker", "by_another"])
def test_release_ends_the_claim_as_released_by_whoever_released_it(
    releaser, client, db_session, mock_matrix_db
):
    """The case DATA-M22-2 exists for: the ticket forgets who had it, and
    the claim does not."""
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    assert start(client, tid).status_code == 200
    started_at = ticket(db_session, tid).started_at

    assert release(client, tid, who=releaser).status_code == 200

    (claim,) = claims(db_session, tid)
    t = ticket(db_session, tid)
    assert t.assigned_to_id is None and t.started_at is None
    assert claim.user_id == user_named(db_session, "u_tech_a").id
    assert claim.started_at == started_at
    assert claim.ended_at is not None and claim.ended_at >= started_at
    assert claim.ended_by_id == user_named(db_session, releaser).id
    assert claim.outcome == ClaimOutcome.RELEASED.value


def test_taking_an_open_ticket_that_still_has_an_open_claim_ends_that_claim(
    client, db_session, mock_matrix_db, ticking_clock
):
    """No route leaves an Open ticket with an open claim, but a hand edit
    can (a status set back to Open in SQL). Starting that ticket used to hit
    uq_ticket_claims_open_ticket and answer 500, and release refuses an Open
    ticket, so the ticket could never be taken again. The take now ends the
    leftover claim first, as release would: Released, by the taker, when the
    new claim starts."""
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    other = user_named(db_session, "u_master").id
    _claim_sql(db_session, tid, other)

    assert start(client, tid).status_code == 200

    leftover, taken = claims(db_session, tid)
    taker = user_named(db_session, "u_tech_a").id
    assert leftover.user_id == other
    assert (leftover.ended_by_id, leftover.outcome) == (taker, ClaimOutcome.RELEASED.value)
    assert leftover.ended_at == taken.started_at == ticket(db_session, tid).started_at
    assert (taken.user_id, taken.ended_at) == (taker, None)


def test_every_take_is_kept_and_listed_newest_first(client, db_session, mock_matrix_db):
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    assert start(client, tid, who="u_tech_a").status_code == 200
    assert release(client, tid, who="u_tech_a").status_code == 200
    assert start(client, tid, who="u_bat_tech").status_code == 200

    rows = claims(db_session, tid)
    api = listed(client)[tid]["claims"]

    assert len(rows) == 2
    assert [c["id"] for c in api] == [rows[1].id, rows[0].id]
    newest, oldest = api
    assert (newest["user_name"], newest["outcome"], newest["ended_at"]) == ("Tech Bat 1", None, None)
    assert (oldest["user_name"], oldest["outcome"], oldest["ended_by_name"]) == (
        "Tech Co A", ClaimOutcome.RELEASED.value, "Tech Co A",
    )


def test_two_takes_from_the_same_instant_are_listed_newest_row_first(
    client, db_session, mock_matrix_db
):
    """The claims relationship orders by started_at, then by id: a tie in
    started_at still lists the later row first."""
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    user = user_named(db_session, "u_master").id
    ended = {"e": "2026-10-19 09:00:00.000000", "b": user, "o": ClaimOutcome.RELEASED.value}
    _claim_sql(db_session, tid, user, **ended)
    _claim_sql(db_session, tid, user, **ended)

    api = listed(client)[tid]["claims"]

    rows = claims(db_session, tid)
    assert rows[0].started_at == rows[1].started_at
    assert [c["id"] for c in api] == [rows[1].id, rows[0].id]


def test_closing_a_taken_ticket_ends_its_claim_as_closed_by_the_closer(
    client, db_session, mock_matrix_db, ticking_clock
):
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    assert start(client, tid, who="u_tech_a").status_code == 200

    assert close(client, tid, who="u_master").status_code == 200

    (claim,) = claims(db_session, tid)
    t = ticket(db_session, tid)
    assert claim.user_id == user_named(db_session, "u_tech_a").id
    assert claim.ended_by_id == user_named(db_session, "u_master").id == t.technician_id
    assert claim.ended_at == t.closed_at
    assert claim.outcome == ClaimOutcome.CLOSED.value


def test_closing_an_open_ticket_writes_no_claim(client, db_session, mock_matrix_db):
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)

    assert close(client, tid).status_code == 200

    assert claims(db_session, tid) == []


def test_closing_ends_only_that_ticket_s_claim(client, db_session, mock_matrix_db):
    item = item_named(db_session, "SA100")
    closing = open_ticket(client, item.id, "Cracked Housing")
    sibling = open_ticket(client, item.id, "Dead Battery")
    assert start(client, closing).status_code == 200
    assert start(client, sibling).status_code == 200

    assert close(client, closing).status_code == 200

    assert claims(db_session, closing)[0].outcome == ClaimOutcome.CLOSED.value
    (open_claim,) = claims(db_session, sibling)
    assert open_claim.ended_at is None


def test_the_item_route_ends_the_claim_of_every_ticket_of_the_item_and_no_other(
    client, db_session, mock_matrix_db, ticking_clock
):
    item = item_named(db_session, "SA100")
    other = item_named(db_session, "SB200")
    first = open_ticket(client, item.id, "Cracked Housing")
    second = open_ticket(client, item.id, "Dead Battery")
    elsewhere = open_ticket(client, other.id, "Cracked Housing", who="u_master")
    for tid in (first, second):
        assert start(client, tid, who="u_tech_a").status_code == 200
    assert start(client, elsewhere, who="u_master").status_code == 200

    assert fix(client, item.id, who="u_master").status_code == 200

    master = user_named(db_session, "u_master").id
    for tid in (first, second):
        (claim,) = claims(db_session, tid)
        assert (claim.outcome, claim.ended_by_id, claim.ended_at) == (
            ClaimOutcome.CLOSED.value, master, ticket(db_session, tid).closed_at,
        )
    (untouched,) = claims(db_session, elsewhere)
    assert (untouched.ended_at, untouched.ended_by_id, untouched.outcome) == (None, None, None)


def test_a_claim_closed_once_is_not_ended_again(client, db_session, mock_matrix_db):
    """fix_equipment after close_ticket: the second close finds no open
    claim and leaves the first close's end alone."""
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    assert start(client, tid).status_code == 200
    assert close(client, tid, who="u_tech_a").status_code == 200
    before = claim_rows(db_session)

    assert fix(client, item.id, who="u_master").status_code == 200

    assert claim_rows(db_session) == before


@pytest.mark.parametrize("case", sorted(CONFLICTS))
def test_a_409_writes_no_claim(case, client, db_session, mock_matrix_db):
    route, arrange, _detail = CONFLICTS[case]
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id, "Cracked Housing")
    open_ticket(client, item.id, "Dead Battery")
    arrange(client, db_session, tid)
    before = claim_rows(db_session)

    res = ROUTES[route](client, tid, who="u_master")

    assert res.status_code == 409, res.text
    assert claim_rows(db_session) == before


def _assert_agree(db):
    """An In Progress ticket has exactly one open claim, by its assignee and
    from its started_at; every other ticket has none."""
    db.expire_all()
    for t in db.query(models.MaintenanceLog):
        open_claims = [c for c in claims(db, t.id) if c.ended_at is None]
        if t.status == TicketStatus.IN_PROGRESS.value:
            assert len(open_claims) == 1, (t.id, t.status, open_claims)
            assert (open_claims[0].user_id, open_claims[0].started_at) == (
                t.assigned_to_id, t.started_at,
            )
        else:
            assert open_claims == [], (t.id, t.status)


def _walk_every_route(client, db):
    """Take, release, take again and close two tickets of one item through
    every route that writes a claim, checking _assert_agree after each step.
    Returns the two ticket ids."""
    item = item_named(db, "SA100")
    a = open_ticket(client, item.id, "Cracked Housing")
    b = open_ticket(client, item.id, "Dead Battery")
    steps = [
        lambda: start(client, a),
        lambda: start(client, b, who="u_bat_tech"),
        lambda: release(client, a, who="u_master"),
        lambda: start(client, a, who="u_master"),
        lambda: close(client, b),
        lambda: fix(client, item.id, who="u_master"),
    ]
    _assert_agree(db)
    for step in steps:
        res = step()
        assert res.status_code == 200, res.text
        _assert_agree(db)
    return a, b


def test_the_ticket_and_its_claims_agree_after_every_route(client, db_session, mock_matrix_db):
    a, b = _walk_every_route(client, db_session)
    assert len(claims(db_session, a)) == 2 and len(claims(db_session, b)) == 1


# --- the API ---------------------------------------------------------------


def test_the_list_carries_each_claim_with_both_names(client, db_session, mock_matrix_db):
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    untouched = open_ticket(client, item.id, "Dead Battery")
    assert start(client, tid, who="u_tech_a").status_code == 200
    assert release(client, tid, who="u_master").status_code == 200
    tech = user_named(db_session, "u_tech_a")
    master = user_named(db_session, "u_master")

    tickets = listed(client)

    (claim,) = tickets[tid]["claims"]
    assert claim["user_id"] == tech.id and claim["user_name"] == tech.full_name
    assert claim["ended_by_id"] == master.id and claim["ended_by_name"] == master.full_name
    assert claim["outcome"] == ClaimOutcome.RELEASED.value
    assert claim["started_at"] and claim["ended_at"]
    assert tickets[untouched]["claims"] == []


def test_a_claimant_or_ender_with_no_name_does_not_break_the_list(
    client, db_session, mock_matrix_db
):
    """users.full_name is nullable (DATA-L11), so both names are Optional."""
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    assert start(client, tid, who="u_tech_a").status_code == 200
    assert release(client, tid, who="u_master").status_code == 200
    db_session.execute(text(
        "UPDATE users SET full_name = NULL WHERE personal_number IN ('u_tech_a', 'u_master')"
    ))
    db_session.commit()

    (claim,) = listed(client, who="u_bat_tech")[tid]["claims"]

    assert (claim["user_name"], claim["ended_by_name"]) == (None, None)


def test_close_answers_with_the_ticket_as_the_list_shows_it(client, db_session, mock_matrix_db):
    """start and release are test_ticket_claim.py's; this is the route whose
    claim write is a Core UPDATE behind _close_tickets."""
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    assert start(client, tid).status_code == 200

    res = close(client, tid)

    assert res.status_code == 200, res.text
    assert res.json()["claims"][0]["outcome"] == ClaimOutcome.CLOSED.value
    assert res.json() == listed(client, who="u_tech_a")[tid]


@pytest.mark.parametrize("taken", [1, 3])
def test_the_list_reads_every_claim_in_one_query(
    taken, client, db_session, mock_matrix_db, count_queries
):
    """selectinload, not a lazy load per ticket: the statements that read
    ticket_claims do not grow with the number of tickets. count_queries,
    because it expires the shared session first (see its docstring)."""
    item = item_named(db_session, "SA100")
    for n in range(taken):
        tid = open_ticket(client, item.id, f"Fault {n}")
        assert start(client, tid).status_code == 200
        assert release(client, tid).status_code == 200
        assert start(client, tid).status_code == 200

    with count_queries() as log:
        tickets = listed(client)

    assert sum(len(t["claims"]) for t in tickets.values()) == 2 * taken
    reading = [s for s in log.statements if "FROM ticket_claims" in s]
    assert len(reading) == 1, reading


def test_the_list_names_each_taker_and_ender_without_a_query_per_person(
    client, db_session, mock_matrix_db, count_queries
):
    """The two names come joined in with the claims. Loaded lazily, they
    would cost one statement per PERSON (the identity map serves a repeat),
    which a test whose takes are all by one account cannot see. So: list
    once, add tickets taken and released by two more people, list again, and
    the statement count must not move."""
    item = item_named(db_session, "SA100")

    def take_and_release(fault, taker, releaser):
        tid = open_ticket(client, item.id, fault)
        assert start(client, tid, who=taker).status_code == 200
        assert release(client, tid, who=releaser).status_code == 200

    take_and_release("Fault 0", "u_tech_a", "u_tech_a")
    with count_queries() as before:
        listed(client)

    take_and_release("Fault 1", "u_bat_tech", "u_brig_tech")
    take_and_release("Fault 2", "u_brig_tech", "u_bat_tech")
    with count_queries() as after:
        names = {
            (c["user_name"], c["ended_by_name"])
            for t in listed(client).values() for c in t["claims"]
        }

    assert names == {
        ("Tech Co A", "Tech Co A"), ("Tech Bat 1", "Tech Brigade"), ("Tech Brigade", "Tech Bat 1"),
    }
    assert len(after.statements) == len(before.statements), after.statements


# --- what the database refuses -------------------------------------------------


def _claim_sql(db, tid, user, **ended):
    row = {"t": tid, "u": user, "s": "2026-10-19 08:00:00.000000",
           "e": None, "b": None, "o": None, **ended}
    db.execute(text(
        "INSERT INTO ticket_claims (ticket_id, user_id, started_at, ended_at, ended_by_id, outcome) "
        "VALUES (:t, :u, :s, :e, :b, :o)"
    ), row)
    db.commit()


def test_a_ticket_cannot_have_two_open_claims(client, db_session, mock_matrix_db):
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    assert start(client, tid).status_code == 200
    other = user_named(db_session, "u_master").id

    with pytest.raises(IntegrityError) as excinfo:
        _claim_sql(db_session, tid, other)
    db_session.rollback()

    assert "ticket_claims.ticket_id" in str(excinfo.value) or (
        "uq_ticket_claims_open_ticket" in str(excinfo.value)
    )


def test_a_ticket_may_have_many_ended_claims(client, db_session, mock_matrix_db):
    """The index is partial: it is about the OPEN claim, not every claim."""
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    user = user_named(db_session, "u_master").id
    ended = {"e": "2026-10-19 09:00:00.000000", "b": user, "o": ClaimOutcome.RELEASED.value}

    _claim_sql(db_session, tid, user, **ended)
    _claim_sql(db_session, tid, user, **ended)
    _claim_sql(db_session, tid, user)

    assert len(claims(db_session, tid)) == 3


@pytest.mark.parametrize(
    "ended",
    [
        {"e": "2026-10-19 09:00:00.000000"},
        {"b": "self"},
        {"o": ClaimOutcome.CLOSED.value},
        {"e": "2026-10-19 09:00:00.000000", "b": "self"},
        {"b": "self", "o": ClaimOutcome.RELEASED.value},
        {"e": "2026-10-19 09:00:00.000000", "o": ClaimOutcome.RELEASED.value},
    ],
    ids=["only_ended_at", "only_ended_by", "only_outcome", "no_outcome", "no_ended_at", "no_ended_by"],
)
def test_a_half_ended_claim_is_refused(ended, client, db_session, mock_matrix_db):
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    user = user_named(db_session, "u_master").id
    ended = {k: (user if v == "self" else v) for k, v in ended.items()}

    with pytest.raises(IntegrityError) as excinfo:
        _claim_sql(db_session, tid, user, **ended)
    db_session.rollback()

    assert "ck_ticket_claims_ended" in str(excinfo.value)


@pytest.mark.parametrize(
    "column,taker,releaser",
    [("user_id", "u_bat_tech", "u_master"), ("ended_by_id", "u_master", "u_bat_tech")],
)
def test_a_claimant_or_an_ender_cannot_be_deleted(
    column, taker, releaser, client, db_session, mock_matrix_db
):
    """RESTRICT, like every key in models.py. u_bat_tech is named by `column`
    alone: the release cleared the ticket's assignee, and memberships and
    grants are deleted first, so the claim is what refuses."""
    item = item_named(db_session, "SA100")
    tid = open_ticket(client, item.id)
    assert start(client, tid, who=taker).status_code == 200
    assert release(client, tid, who=releaser).status_code == 200
    bat_tech = user_named(db_session, "u_bat_tech").id
    for table in ("group_memberships", "grants"):
        db_session.execute(text(f"DELETE FROM {table} WHERE user_id = :u"), {"u": bat_tech})
    db_session.commit()

    with pytest.raises(IntegrityError):
        db_session.execute(text("DELETE FROM users WHERE id = :u"), {"u": bat_tech})
        db_session.commit()
    db_session.rollback()

    db_session.execute(text(f"DELETE FROM ticket_claims WHERE {column} = :u"), {"u": bat_tech})
    db_session.execute(text("DELETE FROM users WHERE id = :u"), {"u": bat_tech})
    db_session.commit()


# --- the revision ----------------------------------------------------------

REVISION, PARENT = "5e8b1d4f7c29", "7a2f5c9d1e38"


def _seed_parent(engine, timezone=None):
    """Tickets in every state the backfill reads, at the parent revision.

    Raw SQL with timestamps as stored strings, so the backfill's copies can
    be compared as written. 3, 4 and 8 are the hand-edited rows the
    COALESCEs exist for (8 would break the upgrade on ck_ticket_claims_ended
    without one); 6 is the one with no time at all. 9 and 10 were closed
    before the claim's start, by started_at and by the opened_at fallback.
    `timezone` is for Postgres,
    which reads each string as a wall clock in the session's TimeZone.
    """
    with engine.begin() as conn:
        if timezone:
            conn.exec_driver_sql(f"SET TimeZone TO '{timezone}'")
        conn.exec_driver_sql(
            "INSERT INTO users (id, personal_number) VALUES (1, 'taker'), (2, 'closer')"
        )
        conn.exec_driver_sql(
            "INSERT INTO maintenance_logs "
            "(id, description, status, opened_at, closed_at, technician_id, assigned_to_id, started_at) VALUES "
            "(1, 'taken', 'In Progress', '2026-10-01 08:00:00.000000', NULL, NULL, 1, '2026-10-02 08:00:00.000000'), "
            "(2, 'taken then closed', 'Closed', '2026-10-01 08:00:00.000000', '2026-10-03 08:00:00.000000', 2, 1, '2026-10-02 08:00:00.000000'), "
            "(3, 'taken, no start', 'In Progress', '2026-10-01 08:00:00.000000', NULL, NULL, 1, NULL), "
            "(4, 'closed, no closer', 'Closed', '2026-10-01 08:00:00.000000', '2026-10-03 08:00:00.000000', NULL, 1, '2026-10-02 08:00:00.000000'), "
            "(5, 'never taken', 'Open', '2026-10-01 08:00:00.000000', NULL, NULL, NULL, NULL), "
            "(6, 'no time at all', 'In Progress', NULL, NULL, NULL, 1, NULL), "
            "(7, 'closed, never taken', 'Closed', '2026-10-01 08:00:00.000000', '2026-10-03 08:00:00.000000', 2, NULL, NULL), "
            "(8, 'closed, no close time', 'Closed', '2026-10-01 08:00:00.000000', NULL, 2, 1, '2026-10-02 08:00:00.000000'), "
            "(9, 'closed before its start', 'Closed', '2026-10-01 08:00:00.000000', '2026-10-03 08:00:00.000000', 2, 1, '2026-10-04 08:00:00.000000'), "
            "(10, 'closed before it opened', 'Closed', '2026-10-05 08:00:00.000000', '2026-10-03 08:00:00.000000', 2, 1, NULL)"
        )


def _claims_raw(engine):
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(
            "SELECT ticket_id, user_id, started_at, ended_at, ended_by_id, outcome "
            "FROM ticket_claims ORDER BY ticket_id"
        ))]


BACKFILLED = [
    (1, 1, "2026-10-02 08:00:00.000000", None, None, None),
    (2, 1, "2026-10-02 08:00:00.000000", "2026-10-03 08:00:00.000000", 2, "Closed"),
    (3, 1, "2026-10-01 08:00:00.000000", None, None, None),
    (4, 1, "2026-10-02 08:00:00.000000", "2026-10-03 08:00:00.000000", 1, "Closed"),
    # No closed_at: the claim ends when it started, by the closer.
    (8, 1, "2026-10-02 08:00:00.000000", "2026-10-02 08:00:00.000000", 2, "Closed"),
    # Closed before the claim's start: it ends when it started, not before.
    (9, 1, "2026-10-04 08:00:00.000000", "2026-10-04 08:00:00.000000", 2, "Closed"),
    (10, 1, "2026-10-05 08:00:00.000000", "2026-10-05 08:00:00.000000", 2, "Closed"),
]


def test_the_revision_backfills_a_claim_from_what_each_ticket_still_says(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm22_2.db'}")
    _upgrade(engine, PARENT)
    _seed_parent(engine)

    _upgrade(engine, REVISION)

    assert _claims_raw(engine) == BACKFILLED
    engine.dispose()


def test_a_second_run_of_the_revision_inserts_nothing(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'rerun.db'}")
    _upgrade(engine, PARENT)
    _seed_parent(engine)
    _upgrade(engine, REVISION)

    script = ScriptDirectory.from_config(migrations.alembic_config())
    with engine.begin() as conn:
        _rerun_upgrade(conn, script, REVISION)

    assert _claims_raw(engine) == BACKFILLED
    engine.dispose()


def test_the_backfill_runs_on_a_database_that_already_has_the_table(tmp_path):
    """A create_all database from today's models has ticket_claims and may
    hold a taken ticket with no claim; the revision still gives it one."""
    engine = create_engine(f"sqlite:///{tmp_path / 'created.db'}")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO users (id, personal_number) VALUES (1, 'taker')")
        conn.exec_driver_sql(
            "INSERT INTO maintenance_logs (id, description, status, opened_at, assigned_to_id, started_at) "
            "VALUES (1, 'taken', 'In Progress', '2026-10-01 08:00:00.000000', 1, '2026-10-02 08:00:00.000000')"
        )

    script = ScriptDirectory.from_config(migrations.alembic_config())
    with engine.begin() as conn:
        _rerun_upgrade(conn, script, REVISION)

    assert _claims_raw(engine) == [BACKFILLED[0]]
    engine.dispose()


def test_the_revision_builds_the_table_the_models_declare(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'shape.db'}")
    _upgrade(engine, REVISION)

    insp = inspect(engine)
    keys = {
        fk["constrained_columns"][0]: (fk["name"], fk["referred_table"], fk["options"].get("ondelete"))
        for fk in insp.get_foreign_keys("ticket_claims")
    }
    indexes = {ix["name"]: ix for ix in insp.get_indexes("ticket_claims")}
    checks = {c["name"] for c in insp.get_check_constraints("ticket_claims")}
    engine.dispose()

    assert keys == {
        "ticket_id": ("fk_ticket_claims_ticket_id", "maintenance_logs", "RESTRICT"),
        "user_id": ("fk_ticket_claims_user_id", "users", "RESTRICT"),
        "ended_by_id": ("fk_ticket_claims_ended_by_id", "users", "RESTRICT"),
    }
    open_index = indexes["uq_ticket_claims_open_ticket"]
    assert open_index["unique"] and open_index["column_names"] == ["ticket_id"]
    assert "ended_at IS NULL" in str(open_index["dialect_options"]["sqlite_where"])
    assert checks == {"ck_ticket_claims_outcome", "ck_ticket_claims_ended"}


def test_the_downgrade_lands_on_the_parent_s_schema(tmp_path):
    reference = create_engine(f"sqlite:///{tmp_path / 'parent.db'}")
    _upgrade(reference, PARENT)
    at_parent = _schema_snapshot(reference)
    reference.dispose()

    engine = create_engine(f"sqlite:///{tmp_path / 'roundtrip.db'}")
    _upgrade(engine, REVISION)
    at_revision = _schema_snapshot(engine)
    _downgrade(engine, PARENT)
    downgraded = _schema_snapshot(engine)
    _upgrade(engine, REVISION)
    upgraded_again = _schema_snapshot(engine)
    engine.dispose()

    assert at_revision != at_parent
    assert downgraded == at_parent
    assert upgraded_again == at_revision


# --- on Postgres -------------------------------------------------------------

@pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL is not set")
def test_the_migrated_table_holds_on_postgres(pg_schema):  # noqa: F811
    """The two things that are the dialect's rather than the revision's: the
    time columns are TIMESTAMPTZ, like every clock.UtcDateTime column, and
    the partial index is built from postgresql_where, which SQLite never
    reads. One open claim per ticket, any number of ended ones."""
    _migrate(pg_schema, "head")
    with pg_schema.begin() as conn:
        schema = conn.execute(text("SELECT current_schema()")).scalar_one()
        types = dict(conn.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = 'ticket_claims'"
        ), {"s": schema}).all())
        conn.execute(text("INSERT INTO users (id, personal_number) VALUES (1, 'taker')"))
        conn.execute(text("INSERT INTO maintenance_logs (id, status) VALUES (1, 'In Progress')"))
        ended = (
            "INSERT INTO ticket_claims (ticket_id, user_id, started_at, ended_at, ended_by_id, outcome) "
            "VALUES (1, 1, now(), now(), 1, 'Released')"
        )
        conn.execute(text(ended))
        conn.execute(text(ended))
        conn.execute(text(
            "INSERT INTO ticket_claims (ticket_id, user_id, started_at) VALUES (1, 1, now())"
        ))

    assert types["started_at"].upper() == types["ended_at"].upper() == "TIMESTAMP WITH TIME ZONE"
    with pytest.raises(IntegrityError) as excinfo, pg_schema.begin() as conn:
        conn.execute(text(
            "INSERT INTO ticket_claims (ticket_id, user_id, started_at) VALUES (1, 1, now())"
        ))
    assert "uq_ticket_claims_open_ticket" in str(excinfo.value)


def _utc(stamp):
    return None if stamp is None else datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)


@pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL is not set")
def test_the_revision_fills_re_runs_and_reverses_on_postgres(pg_schema):  # noqa: F811
    """The SQLite tests above, on Postgres and with data, as every earlier
    revision has one in test_utc_migration_postgres.py. The backfill runs
    under a session TimeZone that is not UTC and must still copy each
    instant; a second run emits no DDL and inserts nothing; the downgrade
    drops the table, and the next upgrade fills it again from the tickets."""
    script = ScriptDirectory.from_config(migrations.alembic_config())
    expected = [(t, u, _utc(s), _utc(e), b, o) for t, u, s, e, b, o in BACKFILLED]

    def tables():
        with pg_schema.connect() as conn:
            return set(inspect(conn).get_table_names())

    _migrate(pg_schema, PARENT)
    _seed_parent(pg_schema, timezone="UTC")

    _migrate(pg_schema, REVISION, session_timezone=WRONG_TIMEZONE)
    assert _claims_raw(pg_schema) == expected

    with recorded(pg_schema) as statements, pg_schema.begin() as conn:
        _rerun_upgrade(conn, script, REVISION)
    ddl = [s for s in statements if s.lstrip().upper().startswith(("ALTER", "CREATE", "DROP"))]
    assert ddl == [], f"re-running the revision did its work again: {ddl}"
    assert _claims_raw(pg_schema) == expected

    _migrate(pg_schema, PARENT, backwards=True)
    assert "ticket_claims" not in tables()

    _migrate(pg_schema, "head")
    assert _claims_raw(pg_schema) == expected


@pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL is not set")
class TestTheRoutesOnPostgres:
    """The routes' claim writes on Postgres. test_ticket_close_postgres.py's
    races take no ticket first: its one start loses with a 409, and its
    closes run _end_claims' UPDATE through a subquery of the tickets with no
    open claim to match. So without this no claim is opened or ended on
    Postgres: not the ORM insert, not a row that UPDATE matches, and not the
    TIMESTAMPTZ equality of a claim's end and its ticket's closed_at, which
    the two take from one clock reading."""

    @pytest.fixture
    def db_session(self, pg_schema):  # noqa: F811  (overrides conftest's SQLite session)
        Base.metadata.create_all(bind=pg_schema)
        session = sessionmaker(autocommit=False, autoflush=False, bind=pg_schema)()
        try:
            yield session
        finally:
            session.close()

    def test_the_ticket_and_its_claims_agree_after_every_route(
        self, client, db_session, mock_matrix_db, ticking_clock
    ):
        a, b = _walk_every_route(client, db_session)

        master = user_named(db_session, "u_master").id
        assert [(c.outcome, c.ended_by_id) for c in claims(db_session, a)] == [
            (ClaimOutcome.RELEASED.value, master),
            (ClaimOutcome.CLOSED.value, master),
        ]
        for tid in (a, b):
            assert claims(db_session, tid)[-1].ended_at == ticket(db_session, tid).closed_at
