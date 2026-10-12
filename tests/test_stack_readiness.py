"""INF-H7 -- each service has a healthcheck and starts after the one it needs.

docker-compose.yml used to order its services with the short form of
`depends_on`, which waits for a container to have been started, and no
service had a healthcheck. Now `db` and `frontend` each have one in the
compose file, the backend has the HEALTHCHECK of Dockerfile.backend, and
both `depends_on` entries wait for `service_healthy`. INF-M6's last two
lines, the two Python settings in Dockerfile.backend, are held here as well.

Nothing here needs Docker. The tests read docker-compose.yml, the two
Dockerfiles, the CI workflow, the port in frontend/vite.config.ts and the
source of `wait_for_db` in backend/main.py, and they import the application
for its routes. The backend's check is also run: its command is started with
this interpreter against a stand-in server on the loopback interface.

What is NOT covered here:
  - what Docker does with any of it: that a container is reported healthy or
    unhealthy, that Compose waits, that `--start-interval` is accepted. The
    `compose-stack` job in .github/workflows/ci.yml starts the stack with
    `--wait`, reads the three health states, and stops the database to see
    the backend turn unhealthy. These tests do not run that job. They check
    that its lines are in the script, outside any block that could step
    over them (`reached`, tests/test_frontend_image.py, says what that
    reader does not see);
  - the `db` and `frontend` checks run for real. Their commands are compared
    as text: pg_isready and BusyBox's wget are in the images, not here;
  - the route the backend's check asks. tests/test_health.py holds it;
  - the image `frontend/Dockerfile` builds as its last stage, nginx. It has
    no healthcheck and this stack does not run it;
  - a healthcheck set by whoever starts a container (`docker run
    --health-cmd`, `--no-healthcheck`), or by a second compose file;
  - whether the times are good ones. Each check is held to give all of its
    times and not leave one to Docker's default. The backend's start period
    is compared with how long backend/main.py sleeps between its tries to
    reach a database, and the check's own timeout with Docker's. How long a
    try itself takes and how long the migrations take are not in that sum;
  - a proxy. The backend's check is run here with proxies turned off for
    it. What it does in a container that is given HTTP_PROXY is not
    measured.

PyYAML is imported here. requirements-dev.in names it, so the tests do not
depend on `uvicorn[standard]` bringing it.
"""
import ast
import inspect
import os
import posixpath
import re
import shlex
import subprocess
import sys
import textwrap
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event, Thread
from urllib.parse import urlsplit

import pytest
import yaml

from backend import main
from tests.test_backend_mounts import PROBE_STEP, step_named
from tests.test_container_entrypoint import (
    compose_backend,
    exec_form,
    free_port,
    image_command,
    parse,
    read_dockerfile,
)
from tests.test_frontend_image import PORT as FRONTEND_PORT
from tests.test_frontend_image import build_stages, compose_frontend, reached, read

SERVICES = ("db", "backend", "frontend")
# Who waits for whom, and for nothing else.
WAITS_FOR = {"db": set(), "backend": {"db"}, "frontend": {"backend"}}

PYTHON_SETTINGS = ("PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE")

# Every key of a healthcheck in docker-compose.yml: the command and its five
# times, none left to Docker's default.
COMPOSE_CHECK_KEYS = {"test", "interval", "timeout", "retries", "start_period", "start_interval"}

CHECK_SECONDS = 30


# --- Reading the checks ------------------------------------------------------

def services():
    return yaml.safe_load(read("docker-compose.yml"))["services"]


def image_checks(service):
    """Every HEALTHCHECK the image of a built service has, in order."""
    build = service["build"]
    path = posixpath.normpath(posixpath.join(build["context"], build["dockerfile"]))
    return stage_checks(build_stages(read(*path.split("/"))), build.get("target"))


def stage_checks(stages, target=None):
    """Every HEALTHCHECK of one stage of a Dockerfile, in order.

    From the stage named `target` (or the last one) and from the stages of
    the same file that one continues. The last is the one Docker uses. A
    base image from elsewhere is not read.
    """
    named = {stage["name"]: stage for stage in stages if stage["name"]}
    stage = stages[-1] if target is None else named[target]

    chain = []
    while stage is not None:
        chain.append(stage)
        stage = named.get(stage["base"])
    return [
        arguments
        for stage in reversed(chain)
        for keyword, arguments in stage["steps"]
        if keyword == "HEALTHCHECK"
    ]


IMAGE_CHECK = re.compile(r"(?P<options>(?:--[a-z-]+=\S+\s+)*)CMD\s+(?P<command>\S.*)")


def image_check(arguments):
    """Split a HEALTHCHECK's arguments into ({option: value}, argv).

    `HEALTHCHECK NONE`, which turns an inherited check off, has no CMD and is
    refused, and so is a command in shell form.
    """
    found = IMAGE_CHECK.fullmatch(arguments)
    assert found is not None, f"not `HEALTHCHECK [options] CMD ...`: {arguments}"
    options = dict(option[2:].split("=", 1) for option in found["options"].split())
    argv = exec_form(found["command"])
    assert argv is not None, f"the check is not in exec (JSON array) form: {found['command']}"
    return options, argv


def compose_check(service):
    """The `test` of a service's own healthcheck, as a list, refusing one that is off."""
    check = service["healthcheck"]
    assert not check.get("disable"), "the healthcheck is disabled"
    test = check.get("test")
    assert isinstance(test, list) and len(test) > 1, f"not a command: {test!r}"
    assert test[0] in ("CMD", "CMD-SHELL"), f"not a command: {test!r}"
    return test


def check_of(name):
    """("compose", test) or ("image", (options, argv)) for a service's check.

    A `healthcheck:` in the compose file replaces the image's, so it is read
    first.
    """
    service = services()[name]
    if "healthcheck" in service:
        return "compose", compose_check(service)
    assert "build" in service, f"`{name}` has no healthcheck and no image built here to have one"
    checks = image_checks(service)
    assert checks, f"`{name}` has no healthcheck: none in docker-compose.yml, none in its Dockerfile"
    return "image", image_check(checks[-1])


def seconds(duration):
    """A Docker duration (`90s`, `1m30s`) in seconds. Whole h, m and s only."""
    found = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", duration)
    assert duration and found is not None, f"a duration this reader does not understand: {duration!r}"
    hours, minutes, rest = (int(part or 0) for part in found.groups())
    return hours * 3600 + minutes * 60 + rest


def backend_check():
    source, check = check_of("backend")
    assert source == "image"
    return check


CHECKED_ADDRESS = re.compile(r"urlopen\('([^']+)', timeout=(\d+)\)")


def backend_check_program():
    """(the Python the check runs, the address it asks, its own timeout)."""
    _, argv = backend_check()
    assert argv[:2] == ["python", "-c"] and len(argv) == 3, argv
    asked = CHECKED_ADDRESS.findall(argv[2])
    assert len(asked) == 1, f"expected one urlopen with a timeout: {argv[2]}"
    return argv[2], urlsplit(asked[0][0]), int(asked[0][1])


def wait_for_db_numbers():
    """The constants backend/main.py's wait_for_db assigns, by name."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(main.wait_for_db)))
    return {
        target.id: node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


# --- The readers themselves --------------------------------------------------

def test_the_check_readers_understand_the_shapes_they_are_asked_about():
    # Hand-written, so they get their own check: a reader that took `NONE`
    # or a disabled check for a check would make the tests below pass.
    options, argv = image_check('--interval=10s --retries=3 CMD ["python", "-c", "pass"]')
    assert options == {"interval": "10s", "retries": "3"}
    assert argv == ["python", "-c", "pass"]
    assert image_check('CMD ["true"]') == ({}, ["true"])
    for refused in ("NONE", "CMD curl -f http://localhost/", "--interval=10s", ""):
        with pytest.raises(AssertionError):
            image_check(refused)

    assert compose_check({"healthcheck": {"test": ["CMD", "true"]}}) == ["CMD", "true"]
    for refused in (
        {"test": ["CMD", "true"], "disable": True},
        {"test": ["NONE"]},
        {"test": "NONE"},
        {"test": ["CMD"]},
        {"test": "true"},
        {"interval": "10s"},
    ):
        with pytest.raises(AssertionError):
            compose_check({"healthcheck": refused})

    assert seconds("90s") == 90
    assert seconds("1m30s") == 90
    assert seconds("1h") == 3600
    for refused in ("", "90", "1.5s", "500ms"):
        with pytest.raises(AssertionError):
            seconds(refused)


def test_the_image_reader_follows_a_stage_into_the_one_it_continues():
    text = "\n".join([
        "FROM node:24-alpine AS dev",
        "HEALTHCHECK CMD one",
        "FROM dev AS build",
        "HEALTHCHECK NONE",
        "FROM nginx:alpine",
        "FROM build",
        "HEALTHCHECK CMD two",
    ])
    stages = build_stages(text)

    # The stage asked for, and not a later one.
    assert stage_checks(stages, "dev") == ["CMD one"]
    # A stage that continues another has that one's checks before its own.
    assert stage_checks(stages, "build") == ["CMD one", "NONE"]
    # No target is the last stage, through both stages it continues.
    assert stage_checks(stages) == ["CMD one", "NONE", "CMD two"]
    # A stage on an image from elsewhere starts with none.
    assert stage_checks(stages[:3]) == []

    # In the real files: the frontend service names a stage, `dev`, which
    # has no HEALTHCHECK, so that service's check has to be the compose
    # file's.
    assert compose_frontend()["build"]["target"] == "dev"
    assert image_checks(compose_frontend()) == []


# --- Every service has a check -----------------------------------------------

def test_these_are_the_services():
    # The lists above name them. A fourth service fails here, so that it is
    # given a check and a place in the order.
    assert set(services()) == set(SERVICES) == set(WAITS_FOR)


@pytest.mark.parametrize("name", SERVICES)
def test_every_service_has_a_healthcheck_that_is_not_turned_off(name):
    source, _ = check_of(name)

    # Where each one is, as decided: the backend's belongs to its image, so
    # the image has it when it is run alone.
    assert source == ("image" if name == "backend" else "compose")


def test_the_backend_image_has_one_healthcheck_and_the_service_does_not_replace_it():
    checks = [arguments for keyword, arguments in read_dockerfile() if keyword == "HEALTHCHECK"]
    assert len(checks) == 1
    assert "healthcheck" not in compose_backend()


# --- The order ---------------------------------------------------------------

@pytest.mark.parametrize("name", SERVICES)
def test_a_service_waits_for_what_it_needs_to_be_healthy(name):
    needs = services()[name].get("depends_on", {})

    # The long form. The short one, a list, waits for a container to have
    # been started and nothing more.
    assert isinstance(needs, dict), f"`{name}` uses the short form of depends_on: {needs!r}"
    assert set(needs) == WAITS_FOR[name]
    for needed, how in needs.items():
        # Exactly this: `service_started` is the short form spelled out, and
        # `required: false` lets the service start without the other.
        assert how == {"condition": "service_healthy"}, f"{name} -> {needed}: {how!r}"
        # And the other has a check to wait for.
        check_of(needed)


# --- The backend's check -----------------------------------------------------

def test_the_backend_check_asks_the_health_route_on_the_port_the_server_listens_on():
    _, address, _ = backend_check_program()

    assert address.scheme == "http"
    # From inside the container, where the server is on the loopback
    # interface whatever the host maps the port to.
    assert address.hostname == "127.0.0.1"
    # Both commands: the image's own, and the one the compose file replaces
    # it with.
    assert address.port == parse(image_command()).params["port"]
    assert address.port == parse(compose_backend()["command"]).params["port"]

    answering_gets = [
        route for route in main.app.routes
        if getattr(route, "path", None) == address.path and "GET" in route.methods
    ]
    assert len(answering_gets) == 1
    # The route that queries the database, and not `/`, which answers
    # without one.
    assert answering_gets[0].endpoint is main.read_health
    assert not address.query and not address.fragment


class Answer(BaseHTTPRequestHandler):
    """Answers every GET with the server's status. A HEAD gets http.server's 501.

    With no status it takes the request and says nothing until the test is
    over.
    """

    def do_GET(self):
        self.server.asked.append(self.path)
        if self.server.status is None:
            self.server.over.wait(CHECK_SECONDS)
            return
        self.send_response(self.server.status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@contextmanager
def answering(status):
    server = HTTPServer(("127.0.0.1", 0), Answer)
    server.status = status
    server.asked = []
    server.over = Event()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.over.set()
        server.shutdown()
        server.server_close()
        thread.join()


def run_the_backend_check(port):
    """Run the check's program with this interpreter, asking `port` instead."""
    program, address, _ = backend_check_program()
    moved = program.replace(f":{address.port}/", f":{port}/")
    assert moved != program
    # urllib sends a request through the proxy the environment names, and on
    # Windows through the system's. Neither is what is measured here.
    environment = {name: value for name, value in os.environ.items() if name.lower() != "no_proxy"}
    environment["no_proxy"] = "*"
    # No `check`: the exit code is what the caller reads.
    return subprocess.run(
        [sys.executable, "-c", moved],
        check=False, capture_output=True, timeout=CHECK_SECONDS, env=environment,
    )


def test_the_backend_check_succeeds_on_a_200():
    _, address, _ = backend_check_program()
    with answering(200) as server:
        assert run_the_backend_check(server.server_port).returncode == 0
        # One request, a GET (the stand-in answers nothing else), for the
        # path the check names.
        assert server.asked == [address.path]


@pytest.mark.parametrize("status", [503, 500, 404])
def test_the_backend_check_fails_on_an_error_status(status):
    # 503 is what /health answers when the query fails. Docker reads the
    # exit code and nothing else.
    with answering(status) as server:
        assert run_the_backend_check(server.server_port).returncode != 0
        assert len(server.asked) == 1


def test_the_backend_check_fails_when_nothing_listens():
    assert run_the_backend_check(free_port()).returncode != 0


def test_the_backend_check_fails_when_the_server_takes_the_request_and_does_not_answer():
    # Ended by the check's own timeout: the harness's limit is far longer,
    # and reaching it raises here.
    with answering(None) as server:
        assert run_the_backend_check(server.server_port).returncode != 0
        assert len(server.asked) == 1


def test_the_backend_check_has_a_timeout_shorter_than_dockers():
    # Compared as written. The check's is a limit on each wait for the
    # server, not on the whole run, which starts an interpreter first.
    options, _ = backend_check()
    _, _, own_timeout = backend_check_program()

    assert 0 < own_timeout < seconds(options["timeout"])


def test_the_start_period_is_longer_than_the_sleeps_of_the_wait_for_a_database():
    options, _ = backend_check()
    numbers = wait_for_db_numbers()
    source = inspect.getsource(main.wait_for_db)

    # The two numbers are the ones the loop uses.
    assert "range(max_retries)" in source
    assert "time.sleep(retry_interval)" in source
    waited = numbers["max_retries"] * numbers["retry_interval"]
    assert waited > 0

    # A failed check inside the start period is not counted. The period
    # covers the sleeping; the tries themselves take time on top of it.
    assert seconds(options["start-period"]) > waited
    # The other times are written out, not left to Docker's defaults.
    assert set(options) == {"interval", "timeout", "start-period", "start-interval", "retries"}
    assert seconds(options["start-interval"]) < seconds(options["interval"])
    assert int(options["retries"]) >= 1


# --- The other two checks ----------------------------------------------------

@pytest.mark.parametrize("name", ["db", "frontend"])
def test_a_compose_check_gives_its_command_and_every_time(name):
    check = services()[name]["healthcheck"]

    # The whole key set: no `disable`, and no time left out.
    assert set(check) == COMPOSE_CHECK_KEYS
    assert seconds(check["start_interval"]) < seconds(check["interval"])
    assert seconds(check["timeout"]) > 0 and seconds(check["start_period"]) > 0
    assert check["retries"] >= 1

def test_the_database_check_reads_the_containers_own_variables():
    source, test = check_of("db")
    assert source == "compose"
    assert test[0] == "CMD-SHELL" and len(test) == 2

    # Compose replaces `$NAME` and `${NAME}` with the HOST's value before
    # the container sees the line, and passes `$$` on as one `$`. So every
    # `$` here is doubled.
    assert "$" not in test[1].replace("$$", "")
    in_the_container = shlex.split(test[1].replace("$$", "$"))
    # Over TCP: on a new volume the image's setup server listens on the Unix
    # socket only, and without -h that one is reported as ready.
    assert in_the_container == [
        "pg_isready", "-h", "127.0.0.1", "-U", "$POSTGRES_USER", "-d", "$POSTGRES_DB",
    ]

    # And the container has both variables.
    given = {entry.split("=", 1)[0] for entry in services()["db"]["environment"]}
    assert {"POSTGRES_USER", "POSTGRES_DB"} <= given


def test_the_frontend_check_asks_the_port_the_development_server_listens_on():
    source, test = check_of("frontend")
    assert source == "compose"
    # Exec form: no shell between Docker and the program.
    assert test[:4] == ["CMD", "wget", "-q", "--spider"] and len(test) == 5

    address = urlsplit(test[4])
    assert (address.scheme, address.hostname, address.path) == ("http", "127.0.0.1", "/")
    assert str(address.port) == FRONTEND_PORT
    assert re.search(rf"\bport:\s*{address.port}\b", read("frontend", "vite.config.ts"))
    assert compose_frontend()["ports"] == [f"{address.port}:{address.port}"]


# --- INF-M6: the two Python settings -----------------------------------------

def test_the_image_sets_both_python_settings_before_its_command():
    instructions = read_dockerfile()
    keywords = [keyword for keyword, _ in instructions]
    command_at = keywords.index("CMD")

    for name in PYTHON_SETTINGS:
        places = [
            position for position, (keyword, arguments) in enumerate(instructions)
            if keyword == "ENV" and name in arguments
        ]
        assert len(places) == 1, f"expected one ENV line for {name}, found {len(places)}"
        # One variable to a line, set to 1.
        assert instructions[places[0]][1] == f"{name}=1"
        assert places[0] < command_at


def test_the_compose_file_does_not_take_the_python_settings_back():
    # An `environment:` entry replaces the image's value, and an empty one
    # turns the setting off.
    names = {entry.split("=", 1)[0] for entry in compose_backend()["environment"]}
    assert not names & set(PYTHON_SETTINGS)


# --- The CI probes -----------------------------------------------------------

HEALTH_OF = 'test "$(docker inspect --format "$health" "$(docker compose ps -q {})")" = healthy'

# After the database is stopped, in this order, and nothing after them.
LAST = [
    "docker compose stop db",
    "for attempt in $(seq 1 90); do",
    "done",
    'test "$(docker inspect --format "$health" "$container")" = unhealthy',
    "status=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 \"$api/health\")",
    'test "$status" = 503',
]


def test_the_probe_script_reads_the_health_of_every_container():
    script = step_named(PROBE_STEP)["run"]
    always_run = reached(script)

    for line in (
        "health='{{.State.Health.Status}}'",
        *(HEALTH_OF.format(name) for name in SERVICES),
        'curl -fsS -o /dev/null "$api/health"',
        'test "$(docker run --rm "$image" printenv PYTHONUNBUFFERED)" = 1',
        'test "$(docker run --rm "$image" printenv PYTHONDONTWRITEBYTECODE)" = 1',
    ):
        assert always_run.count(line) == 1, f"the probe script lost, or may skip: {line}"

    # Each name is given its value once, on a line that always runs, and
    # `name=` is nowhere else in the script: not in a loop, not after a `;`.
    # (`read`, `declare` and the like are not looked for.)
    for name, value in (
        ("health", "health='{{.State.Health.Status}}'"),
        ("container", "container=$(docker compose ps -q backend)"),
        ("image", "image=$(docker inspect --format '{{.Image}}' \"$container\")"),
    ):
        assert always_run.count(value) == 1
        assert len(re.findall(rf"(?<![\w$]){name}=", script)) == 1, name


def test_the_probe_script_ends_by_stopping_the_database_and_reading_the_backend():
    script = step_named(PROBE_STEP)["run"]
    always_run = reached(script)

    # The end of the script. The probes above it were written for a stack
    # with its database running, so none comes after this.
    assert always_run[-len(LAST):] == LAST
    assert always_run.count("docker compose stop db") == 1

    # The loop between them waits for the state the line after it requires,
    # and only waits: it leaves when the state is there.
    commands = [line.strip() for line in script.splitlines() if line.strip()]
    start = commands.index("docker compose stop db")
    assert commands[start + 1:start + 5] == [
        "for attempt in $(seq 1 90); do",
        'if test "$(docker inspect --format "$health" "$container")" = unhealthy; then break; fi',
        "sleep 1",
        "done",
    ]
