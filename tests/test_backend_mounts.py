"""INF-H3 -- the development stack mounts source paths, not the repository.

docker-compose.yml used to give the backend service `./:/app`. That replaced
everything Dockerfile.backend had copied into /app with the host's working
tree, so what .dockerignore keeps out of the image -- .env, .git, the local
database file -- was in the container anyway (SEC-M15), and the container
could write to all of it. The service now mounts the three paths the server
loads, each read-only.

Nothing here needs Docker. The tests read docker-compose.yml,
Dockerfile.backend, .dockerignore and the CI workflow, and compare the mounts
with where the `backend` package is and where backend/migrations.py and
alembic.ini say the migrations are.

What is NOT covered here:
  - what Docker does with the mounts. The `compose-stack` job in
    .github/workflows/ci.yml starts the stack and looks inside the backend
    container, and is the only check of that. These tests do not run the
    job. They check that it exists, has no `if` or `continue-on-error` on the
    job or on a step, starts the stack from docker-compose.yml alone, and
    still contains each of its probe lines;
  - a file that should not be in a container but sits inside a mounted
    directory, such as a backend/.env. The .dockerignore patterns are
    compared with the mount sources, not with the files under them;
  - the frontend service, which still mounts the whole of ./frontend;
  - .dockerignore patterns containing `**` or starting with `!`. The file
    has neither, and a test fails if it gains one.

PyYAML is imported here and is not named in requirements.txt; it arrives
through `uvicorn[standard]`.
"""
import fnmatch
import os
import posixpath
import re

import yaml

from tests.test_container_entrypoint import compose_backend, read_dockerfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CI_JOB = "compose-stack"
PROBE_STEP = "Probe the backend container"

# Compose's short syntax puts these after the second colon.
ACCESS_MODES = {"ro", "rw"}


def read(*parts):
    with open(os.path.join(PROJECT_ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


# --- Reading the mounts ------------------------------------------------------

def mount(entry):
    """One `volumes:` entry as a dict: type, source, target, read_only.

    Understands both of Compose's spellings. In the short one a source that
    starts with `.`, `/` or `~` is a path on the host, and anything else is
    the name of a volume. A Windows drive path, whose own colon the short
    syntax cannot tell from a separator, is refused rather than guessed at.
    """
    if isinstance(entry, dict):
        return {
            "type": entry["type"],
            "source": entry.get("source"),
            "target": entry["target"],
            "read_only": entry.get("read_only", False),
        }

    parts = entry.split(":")
    assert 1 <= len(parts) <= 3, f"a mount this reader does not understand: {entry!r}"
    assert not re.fullmatch(r"[A-Za-z]", parts[0]), f"a drive letter, not a source: {entry!r}"
    if len(parts) == 1:
        return {"type": "volume", "source": None, "target": parts[0], "read_only": False}

    source, target = parts[0], parts[1]
    options = set(parts[2].split(",")) if len(parts) == 3 else set()
    assert len(options & ACCESS_MODES) <= 1, f"two access modes: {entry!r}"
    return {
        "type": "bind" if source.startswith((".", "/", "~")) else "volume",
        "source": source,
        "target": target,
        "read_only": "ro" in options,
    }


def backend_mounts():
    return [mount(entry) for entry in compose_backend().get("volumes") or []]


def in_the_repository(source):
    """A mount source as a path relative to the repository, `/`-separated.

    `.` is the repository itself. Compose resolves a relative source against
    the directory of the compose file, which is the repository root.
    """
    assert source.startswith("./") or source == ".", f"not relative to the compose file: {source!r}"
    relative = posixpath.normpath(source)
    assert not relative.startswith(".."), f"outside the repository: {source!r}"
    return relative


def working_directory():
    directories = [args for keyword, args in read_dockerfile() if keyword == "WORKDIR"]
    assert len(directories) == 1, f"expected one WORKDIR, found {directories}"
    return directories[0]


def under(path, directory):
    """Whether `path` is `directory` or something inside it."""
    return path == directory or path.startswith(directory.rstrip("/") + "/")


# --- Reading .dockerignore ---------------------------------------------------

def excluded_from_the_image():
    """The patterns of the backend's .dockerignore, each as its components.

    A pattern is matched from the root of the build context: `.env` is the
    file beside the Dockerfile and not one further down. A trailing `/` says
    the pattern names a directory and is dropped.
    """
    lines = [line.strip() for line in read(".dockerignore").splitlines()]
    patterns = [line for line in lines if line and not line.startswith("#")]
    for pattern in patterns:
        assert "**" not in pattern and not pattern.startswith("!"), (
            f"a pattern this reader does not understand: {pattern!r}"
        )
    return [pattern.strip("/").split("/") for pattern in patterns]


def puts_back(relative, pattern):
    """Whether mounting `relative` brings in something `pattern` keeps out.

    It does when the pattern matches the path or a directory above it, and
    when the path is a directory above what the pattern names.
    """
    if relative == ".":
        return True
    parts = relative.split("/")
    shared = min(len(parts), len(pattern))
    return all(fnmatch.fnmatchcase(parts[i], pattern[i]) for i in range(shared))


# --- Reading the CI job ------------------------------------------------------

def ci_job():
    jobs = yaml.safe_load(read(".github", "workflows", "ci.yml"))["jobs"]
    assert CI_JOB in jobs, f"ci.yml has no `{CI_JOB}` job"
    return jobs[CI_JOB]


def step_named(name):
    steps = [step for step in ci_job()["steps"] if step.get("name") == name]
    assert len(steps) == 1, f"expected one step named `{name}`, found {len(steps)}"
    return steps[0]


def script_lines(step):
    return [line.strip() for line in step["run"].splitlines() if line.strip()]


# --- The mounts --------------------------------------------------------------

def test_the_backend_service_mounts_the_three_source_paths_read_only():
    found = sorted(backend_mounts(), key=lambda item: item["target"])

    assert found == [
        {"type": "bind", "source": "./alembic", "target": "/app/alembic", "read_only": True},
        {"type": "bind", "source": "./alembic.ini", "target": "/app/alembic.ini", "read_only": True},
        {"type": "bind", "source": "./backend", "target": "/app/backend", "read_only": True},
    ]


def test_no_mount_covers_the_application_directory_or_can_be_written_through():
    # The list above, said as rules, so that a deliberate change to the list
    # still meets them.
    app = working_directory()
    found = backend_mounts()
    assert found, "the development stack is expected to mount its source"

    for item in found:
        assert item["type"] == "bind", item
        assert item["read_only"] is True, item

        relative = in_the_repository(item["source"])
        # The whole repository is what this ticket removed.
        assert relative != ".", item
        assert os.path.exists(os.path.join(PROJECT_ROOT, *relative.split("/"))), item

        # Each path sits in the container where the image has it, so the
        # mount replaces that one path. A target that is /app, or above it,
        # would replace everything the image built there.
        assert item["target"] == posixpath.join(app, relative), item
        assert not under(app, item["target"]), item


def test_the_package_and_the_migrations_are_each_under_a_mount():
    # Otherwise an edit to it would not reach the running server, and the
    # container would go on running the copy made when the image was built.
    import backend
    from backend import migrations

    config = migrations.alembic_config()
    loaded = [
        os.path.dirname(os.path.abspath(backend.__file__)),
        config.config_file_name,
        # alembic.ini gives this with %(here)s, so it arrives absolute.
        config.get_main_option("script_location"),
    ]
    mounted = [in_the_repository(item["source"]) for item in backend_mounts()]

    for path in loaded:
        relative = os.path.relpath(path, PROJECT_ROOT).replace(os.sep, "/")
        assert not relative.startswith(".."), path
        assert any(under(relative, source) for source in mounted), (
            f"{relative} is loaded by the server and no mount provides it"
        )


def test_no_mount_puts_back_what_the_build_context_leaves_out():
    patterns = excluded_from_the_image()
    # The three this ticket and SEC-M15 name. If .dockerignore stopped
    # listing them the loop below would have nothing to say about them.
    for kept_out in ([".env"], [".git"], ["*.db"]):
        assert kept_out in patterns, kept_out

    for item in backend_mounts():
        relative = in_the_repository(item["source"])
        for pattern in patterns:
            assert not puts_back(relative, pattern), (
                f"mounting {item['source']} brings in what `{'/'.join(pattern)}` excludes"
            )


def test_the_reader_of_dockerignore_sees_the_mount_this_ticket_removed():
    # The premise of the test above: it would have failed on `./:/app`, and
    # fails on the narrower ways of bringing the same files in.
    patterns = excluded_from_the_image()

    def brought_in(source):
        relative = in_the_repository(source)
        return any(puts_back(relative, pattern) for pattern in patterns)

    for source in (".", "./", "./.env", "./.git", "./.git/hooks", "./sql_app.db", "./frontend"):
        assert brought_in(source), source
    for source in ("./backend", "./alembic", "./alembic.ini", "./tests"):
        assert not brought_in(source), source


def test_the_backend_service_has_no_other_way_to_bring_host_files_in():
    # The whole key set. `volumes_from`, `env_file`, `secrets`, `configs` and
    # `develop` each put host files in a container without a `volumes:` entry,
    # and `working_dir` would move the directory the mounts are measured
    # against.
    assert set(compose_backend()) == {
        "build", "ports", "depends_on", "environment", "volumes", "restart", "command",
    }


def test_the_image_declares_no_volume_of_its_own():
    # Outside this stack nothing is mounted unless whoever runs the image
    # says so. A VOLUME instruction would create one unasked.
    assert not [args for keyword, args in read_dockerfile() if keyword == "VOLUME"]


# --- The CI job that starts the stack ----------------------------------------

def test_the_ci_job_cannot_be_skipped_or_forgiven():
    job = ci_job()

    # The whole key set, for the job and for each step: no `if`, no
    # `continue-on-error`, and nothing else that changes when or whether a
    # step counts.
    assert set(job) == {"runs-on", "steps"}
    for step in job["steps"]:
        assert set(step) <= {"name", "uses", "run"}, step


def test_the_ci_job_gives_the_stack_every_variable_it_refuses_to_start_without():
    required = set(re.findall(r"\$\{(\w+):\?", read("docker-compose.yml")))
    assert required, "docker-compose.yml is expected to require some variables"

    lines = script_lines(step_named("Write a throwaway .env"))
    # A file named .env, in the checkout: the file whose absence from the
    # container the probes then look for.
    assert lines[0] == "cat > .env <<'EOF'" and lines[-1] == "EOF"
    written = dict(line.split("=", 1) for line in lines[1:-1])

    assert required <= set(written)
    assert all(written.values()), written


def test_the_ci_job_starts_the_stack_as_docker_compose_yml_describes_it():
    steps = [step for step in ci_job()["steps"] if "docker compose up" in step.get("run", "")]
    assert len(steps) == 1
    # No `-f` naming another file, no service list, and a build of both images.
    assert steps[0]["run"].strip() == "docker compose up -d --build"

    # Nor anywhere else in the job: a second file would be a second
    # description of the service, mounts included. `-f`, `-p` and
    # `--env-file` are options of `docker compose` itself and go before the
    # subcommand, so each use is checked to name a subcommand straight away.
    scripts = "\n".join(step.get("run", "") for step in ci_job()["steps"])
    following = re.findall(r"docker compose\s+(\S+)", scripts)
    assert following and not [word for word in following if word.startswith("-")], following
    # The same options as environment variables.
    assert "COMPOSE_" not in read(".github", "workflows", "ci.yml")
    # And the files `docker compose` reads without being told to: an
    # override file is merged in, and compose.yaml is preferred to
    # docker-compose.yml.
    for name in (
        "compose.yaml", "compose.yml", "docker-compose.yaml",
        "compose.override.yaml", "compose.override.yml",
        "docker-compose.override.yaml", "docker-compose.override.yml",
    ):
        assert not os.path.exists(os.path.join(PROJECT_ROOT, name)), name


def test_the_probe_script_is_set_to_stop_on_a_failure():
    lines = script_lines(step_named(PROBE_STEP))
    commands = [line for line in lines if not line.startswith("#")]

    # Without -e a failed probe is just a line of output.
    assert lines[0] == "set -euo pipefail"
    # -e stays on: no later `set` line.
    assert [line for line in commands if line.startswith("set ")] == ["set -euo pipefail"]
    # No `||` but the log dump's.
    assert [line for line in commands if "||" in line] == ["trap 'docker compose logs || true' ERR"]


def test_the_probe_script_still_makes_each_of_its_probes():
    # The probes are shell, and nothing here runs them. This holds each one
    # in the script as a line of its own, outside a comment, so that a probe
    # cannot be dropped or commented out with everything still green. It does
    # not show that a line does what it says.
    commands = [line.strip() for line in step_named(PROBE_STEP)["run"].splitlines()]

    for probe in (
        # the server answers before anything is concluded from the container
        'curl -fsS -o /dev/null "$api/openapi.json"',
        # what is looked for exists on the host, and exec works
        "test -f .env",
        "test -e .git",
        "docker compose exec -T backend test -f /app/backend/main.py",
        # what the build context leaves out is not in the container
        "if docker compose exec -T backend test -e /app/.env; then",
        "if docker compose exec -T backend test -e /app/.git; then",
        "databases=$(docker compose exec -T backend find /app -name '*.db')",
        'test -z "$databases"',
        # the three mounts, read-only
        (
            "test \"$mounts\" = \"$(printf '%s\\n' 'bind /app/alembic rw=false' "
            "'bind /app/alembic.ini rw=false' 'bind /app/backend rw=false')\""
        ),
        "if docker compose exec -T backend touch /app/backend/written-from-the-container; then",
        # a host file reaches the container and restarts the server
        "echo '# Written by the compose-stack CI job.' > backend/reload_probe.py",
        "docker compose exec -T backend test -f /app/backend/reload_probe.py",
        'grep -qF "$notice" backend.log',
        'test "$(grep -c "$started" backend.log)" -gt "$before"',
        # the frontend service is the development server
        'curl -fsS -o ui.html "$ui/"',
        "grep -q '/src/main.tsx' ui.html",
    ):
        assert probe in commands, f"the probe script lost: {probe}"

    # The server is asked twice outside a waiting loop: before anything is
    # concluded from the container, and again after the reload.
    assert commands.count('curl -fsS -o /dev/null "$api/openapi.json"') == 2

    # Each `if` probe ends the script when its condition holds.
    conditions = [line for line in commands if line.startswith("if docker compose exec ")]
    assert len(conditions) == 3
    for line in conditions:
        outcome = commands[commands.index(line) + 1]
        assert outcome.endswith("; exit 1"), outcome
        assert commands[commands.index(line) + 2] == "fi"

    # The mounts are read from the backend container, whichever it is.
    assert "container=$(docker compose ps -q backend)" in commands
    listing = [line for line in commands if line.startswith("mounts=$(docker inspect ")]
    assert len(listing) == 1
    assert "{{range .Mounts}}{{.Type}} {{.Destination}} rw={{.RW}}" in listing[0]
    assert listing[0].endswith('"$container" | grep . | LC_ALL=C sort)')
