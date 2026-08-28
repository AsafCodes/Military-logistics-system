import os
import sqlite3

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Load .env here rather than relying on another module importing security first:
# the alembic CLI reaches this module without importing the app.
load_dotenv()

# Get DB URL from Env (Docker) or fallback to SQLite (Local)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./sql_app.db")


def _enforce_sqlite_foreign_keys(dbapi_connection, connection_record):
    """Turn on foreign key enforcement for a SQLite connection.

    SQLite parses REFERENCES clauses and then ignores them entirely unless this
    pragma is set, and it is off by default, per connection. So every ondelete
    rule the models declare -- all of authz.py's CASCADEs -- is real on Postgres
    and inert here until this runs.

    That gap is not cosmetic. Deleting a Group used to leave its group_edges
    rows behind, and SQLite reuses the rowid of the highest deleted row, so the
    next group created would inherit that debris and silently gain a containment
    nobody declared. Pinned by tests/test_group_schema.py.

    Guarded on the connection type rather than on the URL: Postgres enforces
    foreign keys unconditionally and must never see this statement.
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def create_database_engine(*, enforce_foreign_keys: bool = True):
    """Build an engine for DATABASE_URL.

    `enforce_foreign_keys=False` exists for exactly one caller: the migration
    path. Alembic's batch_alter_table rebuilds a table on SQLite by copying it,
    dropping the original and renaming the copy, and DROP TABLE equipment fails
    outright under enforcement because four tables reference it.

    It has to be a separate engine rather than a pragma toggled off around the
    migration, because SQLite ignores this pragma while a transaction is open
    and reports no error for doing so -- and migrations.py runs Alembic inside
    engine.begin(). A toggle there would silently do nothing.
    """
    connect_args = {}
    if DATABASE_URL.startswith("sqlite"):
        connect_args = {"check_same_thread": False}

    new_engine = create_engine(DATABASE_URL, connect_args=connect_args)
    if enforce_foreign_keys:
        event.listen(new_engine, "connect", _enforce_sqlite_foreign_keys)
    return new_engine


engine = create_database_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- Dependency ---
def get_db():
    """FastAPI dependency for database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
