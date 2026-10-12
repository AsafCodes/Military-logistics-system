"""INF-H7 -- GET /health says whether the server can reach its database.

The route is for the backend container's healthcheck. It executes one
statement and answers 200 or 503. It takes no credentials, and its 503
carries none of the exception's text.

What is NOT covered here:
  - a database that stops answering while a server runs. The failures below
    are a stand-in session that raises, and a real session on a SQLite file
    that cannot be opened. Nothing here stops a Postgres under a running
    server;
  - a database that neither answers nor refuses. The route sets no time
    limit, and how long such a request lasts is not measured here;
  - the transaction around the statement. The statement log below is read
    from cursor events, which a BEGIN or a ROLLBACK does not pass through;
  - a healthcheck calling the route.
"""
import inspect

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import sessionmaker

from backend import main
from backend.database import get_db
from backend.main import app
from tests.conftest import recorded

# Made up, in the shape of a psycopg2 message: an address, a port and an
# account name.
DRIVER_MESSAGE = 'connection to server at "db" (172.18.0.2), port 5432 failed: FATAL: role "stack"'


class StandInSession:
    """Records each statement it is given, then returns or raises."""

    def __init__(self, error=None):
        self.error = error
        self.statements = []

    def execute(self, statement, *args, **kwargs):
        self.statements.append(str(statement))
        if self.error is not None:
            raise self.error


@pytest.fixture
def served_with():
    """A client for the app with `session` as every route's database session."""
    def install(session):
        app.dependency_overrides[get_db] = lambda: session
        # Server errors come back as responses, so a route that let the
        # exception through is a 500 here and not a traceback.
        return TestClient(app, raise_server_exceptions=False)

    yield install
    app.dependency_overrides.pop(get_db, None)


# --- The database answers ----------------------------------------------------

def test_health_is_200_when_the_query_returns(client):
    # `client` is the suite's own: a real session on the in-memory database.
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_executes_one_select_and_nothing_else(client, db_session):
    # Read from the engine's cursor events, so this is every statement the
    # request executed and not what the route handed to a session.
    with recorded(db_session.get_bind()) as statements:
        assert client.get("/health").status_code == 200

    assert statements == ["SELECT 1"]


def test_health_asks_the_session_it_is_given(served_with):
    # The route takes its session from get_db. The stand-in has no database
    # behind it, so the statement it was handed is all the route did here.
    session = StandInSession()

    assert served_with(session).get("/health").status_code == 200
    assert session.statements == ["SELECT 1"]


# --- The database does not ---------------------------------------------------

@pytest.mark.parametrize("error", [
    OperationalError("SELECT 1", {}, Exception(DRIVER_MESSAGE)),
    # What SQLAlchemy raises for a connection the driver has already closed.
    InterfaceError("SELECT 1", {}, Exception(DRIVER_MESSAGE)),
    # Raised by the pool, not by a driver: every connection is in use and
    # none came back in time.
    PoolTimeoutError(f"QueuePool limit of size 5 overflow 10 reached. {DRIVER_MESSAGE}"),
], ids=lambda error: type(error).__name__)
def test_health_is_503_when_sqlalchemy_raises_and_says_nothing_of_why(served_with, error):
    session = StandInSession(error)
    response = served_with(session).get("/health")

    assert session.statements == ["SELECT 1"]
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    for part in ("172.18.0.2", "5432", "stack", "SELECT", "FATAL", "QueuePool"):
        assert part not in response.text, part


def test_health_is_503_on_a_real_session_whose_database_cannot_be_opened(served_with, tmp_path):
    # Not a stand-in: SQLAlchemy's own session, on a SQLite file in a
    # directory that does not exist. The failure comes from the driver, when
    # the route's query asks for a connection.
    url = f"sqlite:///{(tmp_path / 'no-such-directory' / 'health.db').as_posix()}"
    engine = create_engine(url)
    with pytest.raises(OperationalError):
        engine.connect()

    session = sessionmaker(bind=engine)()
    try:
        response = served_with(session).get("/health")
    finally:
        session.close()

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "no-such-directory" not in response.text


def test_health_does_not_turn_another_failure_into_a_503(served_with):
    # Only what SQLAlchemy raises means "the database is unavailable". A
    # fault in the server's own code stays a 500.
    response = served_with(StandInSession(RuntimeError("not the database"))).get("/health")

    assert response.status_code == 500


def test_health_answers_again_once_the_database_does(served_with):
    # Nothing is remembered from one request to the next.
    session = StandInSession(OperationalError("SELECT 1", {}, Exception(DRIVER_MESSAGE)))
    client = served_with(session)
    assert client.get("/health").status_code == 503

    session.error = None

    assert client.get("/health").status_code == 200


# --- Who may ask, and how ----------------------------------------------------

def test_health_takes_no_credentials(client):
    assert not client.cookies
    response = client.get("/health")

    assert "authorization" not in {name.lower() for name in response.request.headers}
    assert response.status_code == 200


def test_health_is_in_the_specification_with_no_security_requirement():
    operation = app.openapi()["paths"]["/health"]["get"]

    assert "security" not in operation


def test_health_is_a_plain_function():
    # FastAPI runs a plain `def` route in a worker thread. Written `async
    # def`, the query would run on the event loop, and a database slow to
    # answer would hold up every other request for as long.
    assert not inspect.iscoroutinefunction(main.read_health)


def test_the_root_route_asks_for_no_session(served_with):
    # `asked` records each time FastAPI resolves get_db for a request, so a
    # root route that took a session and left it unused is seen too.
    asked = []
    session = StandInSession(RuntimeError("the root route used the session"))
    client = served_with(session)
    app.dependency_overrides[get_db] = lambda: asked.append("get_db") or session

    # The premise: the record sees a route that does ask.
    assert client.get("/health").status_code == 500
    assert asked == ["get_db"]

    assert client.get("/").status_code == 200
    assert asked == ["get_db"]
    assert session.statements == ["SELECT 1"]
