"""INF-H1 -- the backend image's own command is a production server.

Dockerfile.backend's only command used to be `uvicorn ... --reload`, and
docker-compose.yml ran the image unchanged, so the development server was what
any use of the image started. The image's command is now the production one
and docker-compose.yml, the development stack, asks for reload itself.

Nothing here needs Docker. The tests read the two files and hand the commands
they find to uvicorn's own argument parser and Config, so that a flag is
judged by what uvicorn makes of it rather than by how it is spelled. The
image's command is then also run as a real server on the loopback interface,
so that what the Dockerfile's comment says about the trust list and the worker
count is measured rather than quoted from help text.

What is NOT covered:
  - that the image builds, or that either stack starts under Docker. No test
    and no CI job builds an image;
  - an ENTRYPOINT inherited from the base image;
  - FORWARDED_ALLOW_IPS set in docker-compose.yml's `environment:`.

PyYAML is imported here and is not named in requirements.txt; it arrives
through `uvicorn[standard]`.
"""
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import time
from contextlib import contextmanager

import click
import pytest
import yaml
from click.core import ParameterSource
from uvicorn import Config
from uvicorn.main import main as uvicorn_cli

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCKERFILE = os.path.join(PROJECT_ROOT, "Dockerfile.backend")
COMPOSE_FILE = os.path.join(PROJECT_ROOT, "docker-compose.yml")

# Environment variables uvicorn reads for itself. Its command line takes any
# option from a UVICORN_<OPTION> variable (the parser's auto_envvar_prefix), and
# its Config reads these two by name. Every test removes all of them from the
# environment it measures in and puts back only what it means to set.
UVICORN_PREFIX = "UVICORN_"
UVICORN_VARIABLES = ("WEB_CONCURRENCY", "FORWARDED_ALLOW_IPS")

SERVER_START_SECONDS = 30


def read_by_uvicorn(name):
    return name.startswith(UVICORN_PREFIX) or name in UVICORN_VARIABLES


# --- Reading the two files ---------------------------------------------------

def dockerfile_instructions(text):
    """Return a Dockerfile's instructions as (KEYWORD, arguments) pairs.

    Understands what this check needs and no more: comment lines, blank lines,
    a backslash joining a line to the next, and any run of spaces or tabs
    between the keyword and its arguments. Heredocs and parser directives are
    not handled; Dockerfile.backend uses neither.
    """
    instructions = []
    pending = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1].rstrip() + " "
            continue
        keyword, *rest = re.split(r"\s+", pending + line, maxsplit=1)
        instructions.append((keyword.upper(), rest[0].strip() if rest else ""))
        pending = ""
    return instructions


def read_dockerfile():
    with open(DOCKERFILE, encoding="utf-8") as handle:
        return dockerfile_instructions(handle.read())


def exec_form(arguments):
    """Return the argv of an exec-form (JSON array) instruction, or None.

    Shell form -- `CMD uvicorn ...` -- is not JSON. Docker hands it to
    `/bin/sh -c` as one string, so what the container starts is a shell and
    there is no argv here to read.
    """
    try:
        argv = json.loads(arguments)
    except ValueError:
        return None
    if isinstance(argv, list) and all(isinstance(item, str) for item in argv):
        return argv
    return None


def image_command():
    """The image's command as an argv, from the one CMD in Dockerfile.backend."""
    instructions = read_dockerfile()
    # One stage, so "the CMD" and "the image's CMD" are the same thing. With a
    # second FROM the CMD could belong to a stage that is not the image.
    stages = [args for keyword, args in instructions if keyword == "FROM"]
    assert len(stages) == 1, f"expected a single build stage, found {stages}"
    commands = [args for keyword, args in instructions if keyword == "CMD"]
    assert len(commands) == 1, f"expected exactly one CMD, found {commands}"
    argv = exec_form(commands[0])
    assert argv is not None, f"CMD is not in exec (JSON array) form: {commands[0]}"
    return argv


def compose_backend():
    with open(COMPOSE_FILE, encoding="utf-8") as handle:
        return yaml.safe_load(handle)["services"]["backend"]


# --- Asking uvicorn what a command means -------------------------------------

@contextmanager
def uvicorn_environment(**variables):
    """Run a block with only the given uvicorn variables set."""
    saved = dict(os.environ)
    for name in saved:
        if read_by_uvicorn(name):
            del os.environ[name]
    os.environ.update(variables)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def parse(argv, **variables):
    """Parse a `uvicorn ...` argv with uvicorn's own command-line parser."""
    assert argv[0] == "uvicorn", f"the command does not start uvicorn: {argv}"
    with uvicorn_environment(**variables):
        return uvicorn_cli.make_context("uvicorn", list(argv[1:]))


def given_on_the_command_line(argv):
    """The names of every option and argument the argv itself supplies."""
    context = parse(argv)
    return {
        name for name in context.params
        if context.get_parameter_source(name) is ParameterSource.COMMANDLINE
    }


def configured(argv, **variables):
    """Build the Config uvicorn would run this argv with, under `variables`.

    Config is where uvicorn falls back to WEB_CONCURRENCY and to
    FORWARDED_ALLOW_IPS; the parser alone reports both as "not given". Only the
    options named below are passed through, which is sound because each
    command is separately required to give no option outside its own short
    list. Building a Config does not import the application.
    """
    options = parse(argv).params
    with uvicorn_environment(**variables):
        return Config(
            options["app"],
            host=options["host"],
            port=options["port"],
            reload=options["reload"],
            workers=options["workers"],
            proxy_headers=options["proxy_headers"],
            forwarded_allow_ips=options["forwarded_allow_ips"],
        )


# --- The image's command -----------------------------------------------------

def test_the_dockerfile_reader_understands_the_shapes_it_is_asked_about():
    # The reader is hand-written, so it gets its own check: an instruction it
    # failed to see would make every "the Dockerfile has no ..." test pass.
    text = "\n".join([
        "# CMD in a comment is not an instruction",
        "FROM python:3.10",
        "",
        "env A=1 \\",
        "    B=2",
        'ENTRYPOINT\t["sh","-c"]',
        '  CMD ["uvicorn", "x:y"]',
        "WORKDIR",
    ])
    assert dockerfile_instructions(text) == [
        ("FROM", "python:3.10"),
        ("ENV", "A=1 B=2"),
        ("ENTRYPOINT", '["sh","-c"]'),
        ("CMD", '["uvicorn", "x:y"]'),
        ("WORKDIR", ""),
    ]
    assert exec_form('["uvicorn", "x:y"]') == ["uvicorn", "x:y"]
    assert exec_form("uvicorn x:y") is None
    assert exec_form('"uvicorn x:y"') is None


def test_the_image_starts_the_application_with_one_exec_form_command():
    argv = image_command()
    options = parse(argv).params

    assert options["app"] == "backend.main:app"
    assert options["host"] == "0.0.0.0"
    assert options["port"] == 8000


def test_the_image_command_gives_these_options_and_no_others():
    # The other tests ask about particular options. This one closes the rest:
    # --env-file (a file uvicorn loads before it reads the trust list), --uds
    # and --fd (which replace the host and port), --factory, --root-path and
    # whatever a later uvicorn adds all have to be put here on purpose.
    assert given_on_the_command_line(image_command()) == {
        "app", "host", "port", "workers", "proxy_headers",
    }


def test_the_image_has_no_entrypoint_of_its_own():
    # With an ENTRYPOINT, that is what the container runs; the CMD is at most
    # its arguments, so the command the other tests read would not be the one
    # that starts.
    keywords = [keyword for keyword, _ in read_dockerfile()]
    assert "ENTRYPOINT" not in keywords


def test_the_image_does_not_watch_files():
    argv = image_command()
    options = parse(argv).params

    assert options["reload"] is False
    # These do nothing without --reload, so finding one here would mean
    # someone expected a watcher.
    for option in ("reload_dirs", "reload_includes", "reload_excludes"):
        assert not options[option], f"--{option.replace('_', '-')} is set"
    assert "reload_delay" not in given_on_the_command_line(argv)
    assert configured(argv).should_reload is False


def test_the_image_runs_one_worker_whatever_the_environment_says():
    argv = image_command()

    # Written out, not left to the default: without the flag uvicorn takes the
    # count from the environment, and backend/main.py migrates once per worker.
    assert "workers" in given_on_the_command_line(argv)
    assert configured(argv).workers == 1
    assert configured(argv, WEB_CONCURRENCY="4").workers == 1
    assert parse(argv, UVICORN_WORKERS="4").params["workers"] == 1


def test_the_image_reads_proxy_headers_and_takes_the_trust_list_from_the_environment():
    argv = image_command()

    # On is also uvicorn's default, so the flag changes nothing today. It is
    # required here so that the setting is written down where the command is.
    assert "proxy_headers" in given_on_the_command_line(argv)
    assert configured(argv).proxy_headers is True
    # No list in the command, so the environment decides and loopback is the
    # fallback. A list written here would be the image's answer for every
    # deployment that runs the command as it stands, and "*" would trust every
    # client.
    assert "forwarded_allow_ips" not in given_on_the_command_line(argv)
    assert configured(argv).forwarded_allow_ips == "127.0.0.1"
    assert configured(argv, FORWARDED_ALLOW_IPS="10.0.0.5").forwarded_allow_ips == "10.0.0.5"


def test_the_image_does_not_set_uvicorns_own_variables():
    # The tests above read the command, and these variables change what the
    # command does without appearing in it: ENV UVICORN_RELOAD=1 turns the
    # watcher on, ENV FORWARDED_ALLOW_IPS=* (or its UVICORN_ spelling) trusts
    # every peer. ENV WEB_CONCURRENCY would be ignored, because the command
    # names the count, and so would only mislead.
    assigned = " ".join(
        arguments for keyword, arguments in read_dockerfile() if keyword in ("ENV", "ARG")
    )
    for name in (UVICORN_PREFIX, *UVICORN_VARIABLES):
        assert name not in assigned


# --- The development stack ---------------------------------------------------

def test_the_development_stack_builds_this_image_and_asks_for_reload_itself():
    backend = compose_backend()
    assert backend["build"]["dockerfile"] == "Dockerfile.backend"

    command = backend.get("command")
    assert isinstance(command, list), f"compose `command` is not a list: {command!r}"
    assert parse(command).params["reload"] is True
    assert configured(command).should_reload is True
    # As for the image: an entrypoint here would be what runs instead.
    assert "entrypoint" not in backend


def test_the_development_stack_serves_the_same_application_on_the_same_address():
    image = parse(image_command()).params
    development = parse(compose_backend()["command"]).params

    for option in ("app", "host", "port"):
        assert development[option] == image[option]


def test_the_development_command_gives_these_options_and_no_others():
    # In particular no worker count: the reloader runs one server process
    # whatever the count says, so a number here would describe a server that
    # is not the one running.
    assert given_on_the_command_line(compose_backend()["command"]) == {
        "app", "host", "port", "reload",
    }


def test_the_development_stack_does_not_steer_uvicorn_through_its_environment():
    # A UVICORN_<OPTION> or WEB_CONCURRENCY entry would change the command
    # without appearing in it. FORWARDED_ALLOW_IPS is deliberately not checked:
    # a stack that gains a proxy has to name it.
    backend = compose_backend()
    # An env_file would be a second way in, and one this test cannot read.
    assert "env_file" not in backend
    entries = backend.get("environment") or []
    if isinstance(entries, dict):
        names = list(entries)
    else:
        names = [entry.split("=", 1)[0] for entry in entries]
    assert names, "the backend service is expected to set some environment"
    for name in names:
        assert not name.startswith(UVICORN_PREFIX) and name != "WEB_CONCURRENCY", name


def test_uvicorns_parser_refuses_what_it_does_not_know():
    # The premise of using the parser at all: a misspelt flag fails the tests
    # above instead of being read as "not set".
    with pytest.raises(click.NoSuchOption):
        parse(["uvicorn", "backend.main:app", "--relaod"])


def test_a_uvicorn_variable_in_the_shell_does_not_reach_these_checks(monkeypatch):
    # The premise of uvicorn_environment: UVICORN_RELOAD=1 really does turn
    # the watcher on for a command that never mentions it, and parse() really
    # does keep the shell's copy out.
    argv = image_command()
    assert parse(argv, UVICORN_RELOAD="1").params["reload"] is True

    monkeypatch.setenv("UVICORN_RELOAD", "1")
    assert parse(argv).params["reload"] is False
    assert os.environ["UVICORN_RELOAD"] == "1"


# --- The image's command, run for real ---------------------------------------

# Stands in for backend.main:app so that no database is involved. It reports
# what the server made of the connection, and whether it is running in the
# process that was started or in one uvicorn spawned from it (a worker pool and
# the reloader both spawn).
#
# The assignment at the top stands in for the application's load_dotenv(): it
# sets the variable when the application is imported, which is after uvicorn
# has built its Config.
PROBE_APPLICATION = '''
import json
import multiprocessing
import os

if os.environ.get("PROBE_SETS_TRUST_LIST_ON_IMPORT"):
    os.environ["FORWARDED_ALLOW_IPS"] = os.environ["PROBE_SETS_TRUST_LIST_ON_IMPORT"]


async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    body = json.dumps({
        "client": scope["client"][0],
        "scheme": scope["scheme"],
        "spawned": multiprocessing.parent_process() is not None,
    }).encode()
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", b"application/json")],
    })
    await send({"type": "http.response.body", "body": body})
'''

FORWARDED = {"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https"}


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def replace_value(argv, flag, value):
    position = argv.index(flag)
    return argv[: position + 1] + [value] + argv[position + 2:]


def stop(server):
    """Stop the server and anything it spawned.

    One process is expected, but a change that makes uvicorn spawn (a worker
    pool, a reloader) is what one of these tests is for. On Windows,
    terminating the parent leaves such children running and holding the port,
    so the whole tree is taken down there. Elsewhere the parent is sent
    SIGTERM and is relied on to stop its own children; that branch has not been
    run: these tests have only been run on Windows so far.
    """
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(server.pid), "/T", "/F"],
            capture_output=True, check=False,
        )
    else:
        server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait(timeout=10)


@contextmanager
def image_command_serving_the_probe(tmp_path, **variables):
    """Start the image's command on the loopback interface; yield a `get`.

    Of the Dockerfile's argv, three values are replaced: the application (the
    probe), the host (loopback, so nothing is exposed and no firewall prompt
    appears) and the port (a free one). `uvicorn` itself is started as
    `python -m uvicorn`, so that it is this interpreter's. Every flag is the
    Dockerfile's. The working directory and the environment are the test's.
    """
    (tmp_path / "probe_application.py").write_text(PROBE_APPLICATION, encoding="utf-8")
    port = free_port()
    argv = image_command()
    assert argv[:2] == ["uvicorn", "backend.main:app"]
    argv = [sys.executable, "-m", "uvicorn", "probe_application:app"] + argv[2:]
    argv = replace_value(argv, "--host", "127.0.0.1")
    argv = replace_value(argv, "--port", str(port))

    environment = {k: v for k, v in os.environ.items() if not read_by_uvicorn(k)}
    environment.update(variables)
    # Output goes to a file, not a pipe: nothing reads a pipe while the server
    # runs, and a full one would block it.
    log_path = tmp_path / "server.log"
    with open(log_path, "w", encoding="utf-8") as log:
        server = subprocess.Popen(
            argv, cwd=tmp_path, env=environment, stdout=log, stderr=subprocess.STDOUT
        )

        def get(headers):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                connection.request("GET", "/", headers=headers)
                return json.loads(connection.getresponse().read())
            finally:
                connection.close()

        try:
            deadline = time.monotonic() + SERVER_START_SECONDS
            while True:
                try:
                    get({})
                    break
                except (OSError, http.client.HTTPException):
                    if server.poll() is not None or time.monotonic() > deadline:
                        log.flush()
                        pytest.fail(
                            "the image's command did not start serving:\n"
                            + log_path.read_text(encoding="utf-8")
                        )
                    time.sleep(0.1)
            yield get
        finally:
            stop(server)


def test_a_loopback_proxy_is_trusted_when_no_trust_list_is_set(tmp_path):
    with image_command_serving_the_probe(tmp_path) as get:
        seen = get(FORWARDED)

    assert seen["client"] == "203.0.113.7"
    assert seen["scheme"] == "https"


def test_a_peer_outside_the_trust_list_cannot_choose_its_address(tmp_path):
    # The request still arrives from loopback; the list now names somewhere
    # else, so loopback is the untrusted peer here.
    with image_command_serving_the_probe(tmp_path, FORWARDED_ALLOW_IPS="10.0.0.5") as get:
        seen = get(FORWARDED)

    assert seen["client"] == "127.0.0.1"
    assert seen["scheme"] == "http"


def test_a_peer_inside_a_listed_network_is_trusted(tmp_path):
    # The other half of "ignored until the variable names it", and the one
    # place a network, rather than a single address, is given.
    with image_command_serving_the_probe(
        tmp_path, FORWARDED_ALLOW_IPS="10.0.0.5,127.0.0.0/8"
    ) as get:
        seen = get(FORWARDED)

    assert seen["client"] == "203.0.113.7"
    assert seen["scheme"] == "https"


def test_an_empty_trust_list_trusts_no_one(tmp_path):
    # Set-but-empty is not the same as unset: .env.example warns that it
    # switches the headers off for loopback too.
    with image_command_serving_the_probe(tmp_path, FORWARDED_ALLOW_IPS="") as get:
        seen = get(FORWARDED)

    assert seen["client"] == "127.0.0.1"
    assert seen["scheme"] == "http"


def test_the_trust_list_is_read_before_the_application_is_imported(tmp_path):
    # Why .env.example says the variable must be in the container's
    # environment: a value that appears only once the application loads (which
    # is when backend/database.py calls load_dotenv()) is too late to be read.
    with image_command_serving_the_probe(
        tmp_path, PROBE_SETS_TRUST_LIST_ON_IMPORT="10.0.0.5"
    ) as get:
        seen = get(FORWARDED)

    assert seen["client"] == "203.0.113.7"


def test_the_server_is_the_process_that_was_started_even_when_asked_for_more(tmp_path):
    with image_command_serving_the_probe(tmp_path, WEB_CONCURRENCY="4") as get:
        answers = [get({}) for _ in range(8)]

    assert not any(answer["spawned"] for answer in answers)
