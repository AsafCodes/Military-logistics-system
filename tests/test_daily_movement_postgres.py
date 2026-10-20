"""The daily movement report reads one group's day, not the whole log, on Postgres.

DATA-M9-2. tests/test_foreign_key_indexes.py holds the report to its two
composite indexes, but on SQLite, and SQLite cannot tell the two ways of
writing the query apart. The route first narrowed the log with
`id IN (union of the day's ids)`, and Postgres planned that by reading every
row of transaction_logs, joining each to the eight eager loads, and only then
keeping the day: 226 ms at 200,000 rows, where joining the union first takes
0.4 ms. SQLite planned both forms alike, so only a Postgres plan shows the
difference, and this module asks for one.

Skipped unless TEST_POSTGRES_URL is set; CI sets it (.github/workflows/ci.yml).
The suite's own fixtures build the data: this module overrides db_session
alone, with a session on an isolated schema (test_utc_migration_postgres's
pg_schema), so group_graph, mock_matrix_db and client run unchanged against
Postgres.
"""
import random
from datetime import timedelta

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import sessionmaker

from backend import clock, models
from backend.database import Base
from tests.conftest import create_auth_header
from tests.test_utc_migration_postgres import POSTGRES_URL, pg_schema  # noqa: F401  (a fixture)

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="TEST_POSTGRES_URL is not set; see this module's docstring",
)

ROWS = 20_000


@pytest.fixture
def db_session(pg_schema):  # noqa: F811  (overrides conftest's SQLite session)
    Base.metadata.create_all(bind=pg_schema)
    session = sessionmaker(autocommit=False, autoflush=False, bind=pg_schema)()
    try:
        yield session
    finally:
        session.close()


def _scans_of(node, table):
    """Every plan node that reads `table`, with the rows it read: the rows it
    returned plus the rows its filter and recheck threw away, times its loops."""
    if node.get("Relation Name") == table:
        read = (
            node.get("Actual Rows", 0)
            + node.get("Rows Removed by Filter", 0)
            + node.get("Rows Removed by Index Recheck", 0)
        )
        yield node["Node Type"], read * node.get("Actual Loops", 1)
    for child in node.get("Plans", []):
        yield from _scans_of(child, table)


def test_the_report_reads_the_day_not_the_log(client, mock_matrix_db, db_session, group_graph):
    rng = random.Random(9)
    company_a, company_b = group_graph["188/53/A"].id, group_graph["188/53/B"].id
    items = dict(db_session.query(models.Equipment.id, models.Equipment.group_id).all())
    tz = clock.zone(clock.DEFAULT_TIMEZONE)
    day = clock.today_in(tz) - timedelta(days=100)
    start, end = clock.day_bounds(tz, day)
    last = clock.utcnow()

    # Two years of movements, a fifth of them transfers between the companies.
    rows = []
    for _ in range(ROWS):
        item = rng.choice(list(items))
        moved = rng.random() < 0.2
        rows.append({
            "equipment_id": item, "event_type": "HANDOVER",
            "timestamp": last - timedelta(seconds=rng.uniform(0, 730 * 86400)),
            "group_id": (company_b if items[item] == company_a else company_a) if moved else items[item],
            "from_group_id": items[item] if moved else None,
        })
    db_session.execute(models.TransactionLog.__table__.insert(), rows)
    db_session.commit()
    # The planner's view of a database old enough for the question to matter;
    # see the SQLite test's NEEDS STATISTICS.
    db_session.execute(text("ANALYZE"))
    db_session.commit()
    in_day = sum(1 for row in rows if start <= row["timestamp"] < end)

    seen = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "transaction_logs" in statement:
            seen.append((statement, parameters))

    bind = db_session.get_bind()
    db_session.expire_all()
    event.listen(bind, "before_cursor_execute", record)
    try:
        response = client.get(
            f"/reports/daily_movement?date={day.isoformat()}", headers=create_auth_header("u_cmdr_a")
        )
    finally:
        event.remove(bind, "before_cursor_execute", record)

    assert response.status_code == 200, response.text
    returned = len(response.json()["items"])
    assert 0 < returned <= in_day, "the seed no longer puts A's rows in the day"
    assert seen, "the report issued no SELECT against transaction_logs, so there is no plan to read"

    conn = db_session.connection()
    for statement, parameters in seen:
        (plan,) = conn.exec_driver_sql("EXPLAIN (ANALYZE, FORMAT JSON) " + statement, parameters).scalar_one()
        scans = list(_scans_of(plan["Plan"], "transaction_logs"))
        assert scans, f"the plan names no read of transaction_logs: {plan}"
        # A day is about 1/730 of the seed; a tenth leaves room for any sane
        # plan and none for reading the table.
        assert all(read <= ROWS // 10 for _, read in scans), (
            f"the report read most of transaction_logs ({ROWS} rows) for one day of "
            f"{returned}: {scans}"
        )
