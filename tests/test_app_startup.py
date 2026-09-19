"""Startup coverage for DATA-H10-1 -- the app imports without a live database.

backend/main.py used to call wait_for_db() and run_migrations() at MODULE
SCOPE, so `import backend.main` connected, blocked up to 60s, and ran Alembic
against whatever DATABASE_URL named. Both now live in a lifespan handler.

The absence tests below only prove that import stopped doing the work.
Deleting the lifespan body outright would also pass them -- and pass the rest
of the suite with them, because no other test enters the app as a context
manager, which is the only thing that starts lifespan. So each absence is
paired with a presence: the work still happens, in the right order, and loudly
when it fails.
"""
import inspect
import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from fastapi.testclient import TestClient

from backend import main
from backend.main import app

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 30 retries x 2s is what the module-scope wait_for_db() cost, so any ceiling
# under 60s proves the wait is gone. A clean import measures ~1.9s on this
# machine; the rest is headroom for a loaded CI box, and is only ever spent
# when the defect has come back.
IMPORT_TIMEOUT_SECONDS = 30


def _import_main(env_overrides, cwd=PROJECT_ROOT):
    """Import backend.main in a fresh interpreter and return the completed process.

    A subprocess because this process has already imported the module and
    sys.modules would hand back the cached one -- the import under test would
    never actually run.

    The environment is built explicitly rather than inherited wholesale for
    DATABASE_URL: conftest.py pins it at the suite's sink database, and
    inheriting that would hand the child a database that opens fine, which is
    the one thing these tests must not do.

    The timeout is caught here rather than in each caller so that both absence
    tests name the defect when it returns, instead of reporting a bare
    TimeoutExpired traceback from subprocess internals.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import backend.main"],
            cwd=cwd,
            env={
                **os.environ,
                "SECRET_KEY": "test_secret_key",
                "PYTHONPATH": os.pathsep.join(
                    p for p in (PROJECT_ROOT, os.environ.get("PYTHONPATH")) if p
                ),
                "PYTHONIOENCODING": "utf-8",
                **env_overrides,
            },
            capture_output=True,
            encoding="utf-8",
            timeout=IMPORT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"importing backend.main blocked for {IMPORT_TIMEOUT_SECONDS}s -- database work "
            "has moved back to module scope (DATA-H10)"
        )

    assert result.returncode == 0, (
        f"importing backend.main failed (exit {result.returncode}):\n{result.stderr}"
    )
    # The readiness banner is part of what this ticket moves: at module scope it
    # announced a running server to anyone who merely imported the app. Nothing
    # else here observes stdout, so without this the banner alone could be moved
    # back with the whole suite still green.
    assert "SYSTEM READY" not in result.stdout, (
        "importing backend.main printed the readiness banner -- it has moved back to "
        "module scope, where it announces a server that is not running (DATA-H10)"
    )
    return result


def test_importing_the_app_does_not_touch_a_database(tmp_path):
    """Importing backend.main must not connect, and must not block trying.

    DATABASE_URL points into a directory that does not exist, so any connection
    attempt fails immediately. A SQLite path rather than an unreachable
    Postgres host on purpose: it needs no driver installed, so the test cannot
    pass or fail for a psycopg2 reason on a developer machine.

    The premise is asserted rather than trusted: the whole test rests on this
    URL being one SQLite refuses, and it must raise OperationalError
    specifically, since that is the exception wait_for_db() catches and retries.
    If some future SQLAlchemy creates intermediate directories instead, the
    import assertion below would quietly degrade into "importing the app works"
    while still reading as coverage.
    """
    url = f"sqlite:///{(tmp_path / 'no-such-directory' / 'vector.db').as_posix()}"

    with pytest.raises(OperationalError):
        create_engine(url).connect()

    _import_main({"DATABASE_URL": url})


def test_importing_the_app_does_not_create_a_database_file(tmp_path):
    """Importing backend.main must not materialise the database it names.

    The other absence test uses a path SQLite cannot open. This one uses a
    relative path it can: the shape a fresh clone gets from database.py's
    `sqlite:///./sql_app.db` fallback. Pre-fix, importing the app anywhere --
    a script, a linter, `python -c` in some unrelated directory -- created a
    fully migrated sql_app.db in whatever the working directory happened to be.
    Verified: the pre-fix import leaves that file behind here, the fixed one
    leaves the directory empty.

    Run from tmp_path so a regression litters the temp directory rather than
    the repository, and DATABASE_URL is set explicitly rather than left to the
    fallback so a developer who adds one to their local .env does not silently
    turn this test into a no-op.
    """
    _import_main({"DATABASE_URL": "sqlite:///./sql_app.db"}, cwd=str(tmp_path))

    assert list(tmp_path.iterdir()) == [], (
        f"importing backend.main created {[p.name for p in tmp_path.iterdir()]} in the working "
        "directory -- startup database work has moved back to module scope (DATA-H10)"
    )


def test_startup_waits_for_the_database_then_migrates_then_serves(monkeypatch):
    """Lifespan startup calls wait_for_db(), then run_migrations(), then serves.

    Order is asserted, not just presence: migrating a database nobody has
    waited for is the failure wait_for_db() exists to prevent, and a set or a
    length check would accept it.

    Patched on backend.main, not on backend.migrations -- main.py does
    `from .migrations import run_migrations`, which binds the function into
    main's own namespace, and rebinding it at the source module would leave the
    handler calling the original.

    TestClient as a CONTEXT MANAGER is what makes any of this run: Starlette
    fires lifespan from __enter__, and a bare TestClient(app) -- the shape every
    other test in this suite uses -- never starts it.

    The request at the end is the point of the ticket's readiness banner: by the
    time startup returns, the app is actually answering, so the banner is a
    statement rather than a guess.
    """
    # Checked before patching, because the stubs below are synchronous and would
    # go on passing if the real ones turned async. The handler calls both without
    # awaiting: a coroutine function there is built, discarded, and never run, so
    # startup would "succeed" having connected to nothing and migrated nothing,
    # announced by a RuntimeWarning that uvicorn's output buries.
    assert not inspect.iscoroutinefunction(main.wait_for_db)
    assert not inspect.iscoroutinefunction(main.run_migrations)

    calls = []
    monkeypatch.setattr(main, "wait_for_db", lambda: calls.append("wait_for_db"))
    monkeypatch.setattr(main, "run_migrations", lambda: calls.append("run_migrations"))

    with TestClient(app) as client:
        assert calls == ["wait_for_db", "run_migrations"]
        assert client.get("/").status_code == 200


def test_startup_refuses_to_serve_when_the_database_never_arrives(monkeypatch):
    """A failure during startup must take the server down, not be swallowed.

    Moving the work into lifespan is only half of what DATA-H10 asks for. The
    other half is that the failure stays fatal: a handler that catches its own
    exception would leave a server answering requests against a database that
    was never reachable and never migrated -- strictly worse than the
    import-time crash it replaced, because nothing announces it.

    run_migrations is patched to record, so this also pins the gating: a
    database that never arrived is never migrated.
    """
    def never_arrives():
        raise RuntimeError("database never arrived")

    migrated = []
    monkeypatch.setattr(main, "wait_for_db", never_arrives)
    monkeypatch.setattr(main, "run_migrations", lambda: migrated.append("run_migrations"))

    with pytest.raises(RuntimeError, match="database never arrived"):
        with TestClient(app):
            pass

    assert migrated == [], "migrations ran against a database wait_for_db never reached"
