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


def _run_import(env_overrides, cwd=PROJECT_ROOT, code="import backend.main"):
    """Run `code` in a fresh interpreter and return the completed process.

    A subprocess because this process has already imported the module and
    sys.modules would hand back the cached one -- the import under test would
    never actually run.

    An override whose value is None REMOVES that variable from the child's
    environment. There is no other way to test a missing variable from a
    parent that has one, and since DATA-H11 the parent always has one:
    conftest.py pins it so the suite can be collected at all.

    DATABASE_URL is always overridden rather than inherited: handing the child
    a database that opens is the one thing these tests must not do.

    Nothing is asserted about the outcome here. The DATA-H11 tests expect a
    non-zero exit, so the success-path assertions live in _import_main below.

    The timeout is caught here rather than in each caller so that the absence
    tests name the defect when it returns, instead of reporting a bare
    TimeoutExpired traceback from subprocess internals.
    """
    env = {
        **os.environ,
        "SECRET_KEY": "test_secret_key",
        "PYTHONPATH": os.pathsep.join(
            p for p in (PROJECT_ROOT, os.environ.get("PYTHONPATH")) if p
        ),
        "PYTHONIOENCODING": "utf-8",
    }
    for name, value in env_overrides.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value

    try:
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=cwd,
            env=env,
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


def _import_main(env_overrides, cwd=PROJECT_ROOT):
    """Import backend.main successfully, or fail the test saying why."""
    result = _run_import(env_overrides, cwd=cwd)

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
    relative path it can: `sqlite:///./sql_app.db`, which until DATA-H11 was
    what database.py defaulted to and is still what .env.example suggests for
    host runs. Pre-fix, importing the app anywhere -- a script, a linter,
    `python -c` in some unrelated directory -- created a fully migrated
    sql_app.db in whatever the working directory happened to be. Verified: the
    pre-fix import leaves that file behind here, the fixed one leaves the
    directory empty.

    Run from tmp_path so a regression litters the temp directory rather than
    the repository. DATABASE_URL is passed explicitly because this test is
    about what the import WRITES, not about what configures it -- since
    DATA-H11 removed the fallback, omitting it would abort the import on a
    configuration error and prove nothing about startup at all.
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


# --- DATA-H11 ---------------------------------------------------------------
# backend/database.py used to default DATABASE_URL to sqlite:///./sql_app.db,
# so a deployment that forgot the variable came up healthy on an ephemeral
# file. It now raises instead. These tests live here because the defect is
# observable only at import, which is what this module already knows how to do.

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))

# Asserted inside the child, before the import under test.
#
# find_dotenv() resolves from the CURRENT DIRECTORY under `python -c` -- with
# no __main__.__file__ it takes its _is_interactive() branch -- so a child run
# from the repo root reads the repo's own .env, which is exactly where a
# developer is told to put DATABASE_URL (.env.example). Every test below that
# claims the variable is missing therefore runs from tmp_path, where the walk
# up to the filesystem root finds no .env.
#
# That is the mechanism; this line is the proof it worked. Without it, a .env
# appearing anywhere above the temp directory -- or find_dotenv changing which
# branch it takes -- would turn these tests into imports that succeed and
# assert nothing, while still reporting as coverage.
#
# The message deliberately avoids the string "DATABASE_URL" so that a premise
# failure cannot satisfy the stderr assertions it precedes.
_ASSERT_NO_DOTENV = (
    "from dotenv import load_dotenv\n"
    "assert not load_dotenv(), 'PREMISE FAILED: a dotenv file supplied configuration'\n"
)


# The child program both conftest tests below run. One copy: the two differ
# only in what they configure, and a fix applied to one spelling of this while
# the other drifted would leave a test measuring something it no longer names.
_PRINT_CONFTEST_URL = (
    "import sys\n"
    f"sys.path.insert(0, {TESTS_DIR!r})\n"
    "import conftest\n"
    "import backend.database\n"
    "print(backend.database.DATABASE_URL)\n"
)


def _assert_premise_held(result):
    """The child really was missing the variable the test claims to remove."""
    assert "PREMISE FAILED" not in result.stderr, (
        "the child read a dotenv file, so the variable was never missing:\n"
        f"{result.stderr}"
    )


@pytest.mark.parametrize("configured", [None, ""], ids=["missing", "empty"])
def test_importing_the_app_refuses_an_unusable_database_url(tmp_path, configured):
    """A missing or empty DATABASE_URL must stop the import, loudly and by name.

    The empty case is not hypothetical and is why the guard is `if not` rather
    than `is None`: .env.example ships the variable empty on purpose -- a
    template carrying a working SQLite URL would reinstate the ephemeral
    database DATA-H11 removed -- and a compose entry whose interpolation
    resolved to nothing produces the same value.
    """
    result = _run_import(
        {"DATABASE_URL": configured},
        cwd=str(tmp_path),
        code=_ASSERT_NO_DOTENV + "import backend.main",
    )

    _assert_premise_held(result)
    assert result.returncode != 0, (
        "importing backend.main succeeded without a usable DATABASE_URL -- the "
        "silent fallback is back (DATA-H11)"
    )
    # Not merely "it crashed", and NOT merely "it said DATABASE_URL" either.
    # Deleting the raise while keeping os.getenv() still exits non-zero -- via
    # "'NoneType' object has no attribute 'startswith'" when the variable is
    # absent, and SQLAlchemy's "Could not parse SQLAlchemy URL from given URL
    # string" when it is empty. Measured: BOTH of those still put the string
    # "DATABASE_URL" in stderr, because the traceback prints the source line
    # `if DATABASE_URL.startswith(...)`. A test asserting only the variable
    # name passes against that mutation, which is how this assertion was first
    # written and what the mutation run caught.
    #
    # So the pointer is what is actually pinned here: an incidental crash names
    # no file to go and edit. That is the difference between a deliberate
    # refusal and a stack trace, and it is the whole complaint DATA-H11 makes.
    assert "DATABASE_URL" in result.stderr, (
        "the import failed without naming DATABASE_URL, so an operator cannot "
        f"tell what to fix:\n{result.stderr}"
    )
    assert ".env.example" in result.stderr, (
        "the import failed naming DATABASE_URL but not where to set it -- this is "
        f"an incidental crash, not a deliberate refusal:\n{result.stderr}"
    )


def test_importing_the_app_still_works_with_a_database_url():
    """The positive control: a guard that refuses everything must fail here.

    Deliberately redundant with the two DATA-H10 imports above, which also pass
    a usable URL and also assert a clean exit. Kept anyway, and cheaply stated:
    those belong to another ticket and may be rewritten by it, and a refusal
    battery whose only positive control lives in someone else's tests is one
    edit away from passing because the guard rejects everything.
    """
    _import_main({"DATABASE_URL": "sqlite://"})


def test_conftest_supplies_a_database_url_when_nothing_else_does(tmp_path):
    """The pin must make the suite collectable on a machine that configures nothing.

    Deleting it is invisible to every other test -- including on the machine
    this was written on, where .env carries DATABASE_URL and load_dotenv hands
    it over before anything notices. The environment this protects is the one
    with no .env and no export: there, conftest.py's own import of
    backend.database raises during COLLECTION and the suite reports zero tests.

    The value is asserted as in-memory rather than as the literal string, so a
    later change of spelling is free but a change of KIND is not: a pin at a
    real file would be a database the suite could silently write to, which is
    the property that matters and the one the empty tmp_path below confirms.
    """
    result = _run_import(
        {"DATABASE_URL": None},
        cwd=str(tmp_path),
        code=_ASSERT_NO_DOTENV + _PRINT_CONFTEST_URL,
    )

    _assert_premise_held(result)
    assert result.returncode == 0, (
        "importing conftest.py with no DATABASE_URL configured anywhere failed -- "
        f"the suite would collect zero tests on such a machine:\n{result.stderr}"
    )
    assert result.stdout.strip() in ("sqlite://", "sqlite:///:memory:"), (
        f"conftest.py pinned DATABASE_URL at {result.stdout.strip()!r}, which names a "
        "file -- the pin must be in-memory so the suite can never write to it"
    )
    assert list(tmp_path.iterdir()) == [], (
        f"importing conftest created {[p.name for p in tmp_path.iterdir()]}"
    )


def test_the_suite_does_not_override_a_configured_database_url(tmp_path):
    """conftest.py must supply a URL only when the environment supplies none.

    The pin there is a setdefault, and nothing else in the suite would notice
    if it became an assignment: every test would still pass, because no test
    connects to the ambient engine. What would change is CI, silently --
    ci.yml names a live Postgres service job-wide, and an assignment would
    replace it with conftest's in-memory SQLite. The engines that address it
    would then be guarding nothing, while refuse_connections_to_the_ambient
    _database went on describing a live database.

    The sentinel is a SQLite URL rather than a Postgres one so the test cannot
    fail for a missing-driver reason, and nothing ever connects to it: the
    empty tmp_path afterwards is what says so.
    """
    sentinel = "sqlite:///sentinel-must-survive-conftest.db"
    result = _run_import(
        {"DATABASE_URL": sentinel},
        cwd=str(tmp_path),
        code=_PRINT_CONFTEST_URL,
    )

    assert result.returncode == 0, f"importing conftest failed:\n{result.stderr}"
    assert result.stdout.strip() == sentinel, (
        "conftest.py replaced a DATABASE_URL the environment had already set -- "
        "its pin must be setdefault, not assignment (DATA-H11)"
    )
    assert list(tmp_path.iterdir()) == [], (
        f"importing conftest created {[p.name for p in tmp_path.iterdir()]} -- something "
        "connected to the ambient database"
    )
