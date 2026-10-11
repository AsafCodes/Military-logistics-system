"""INF-H3 -- the development stack mounts source paths, not the repository.

docker-compose.yml used to give the backend service `./:/app`. That replaced
everything Dockerfile.backend had copied into /app with the host's working
tree, so what .dockerignore keeps out of the image -- .env, .git, the local
database file -- was in the container anyway (SEC-M15), and the container
could write to all of it. The service now mounts the three paths the server
loads, each read-only.

INF-M6, the copy. Dockerfile.backend used to say `COPY . .`, so the image
held whatever the repository held that .dockerignore did not name: the
frontend's source, the tests, and any file or directory that appeared after
the list was written. It now copies the same three paths the service mounts,
by name, and requirements.txt. .dockerignore is left with what can turn up
inside those paths: bytecode, tool caches, a `.env`, a database file.

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
  - .dockerignore patterns starting with `!`, or with `**` anywhere but at
    the front. The file has neither, and a test fails if it gains one;
  - what Docker makes of the patterns and of the COPY lines. They are read
    here by the rules Docker documents. The `compose-stack` job plants a
    bytecode cache, a bytecode file and a `.env` inside backend/ and a file
    beside it, builds the image, and lists what the image holds with nothing
    mounted over it. That is the check against a real build, and it covers
    those four plants and the top of /app, not every pattern.

PyYAML is imported here and is not named in requirements.txt; it arrives
through `uvicorn[standard]`.
"""
import fnmatch
import os
import posixpath
import re

import yaml

from tests.test_container_entrypoint import compose_backend, read_dockerfile
from tests.test_frontend_image import reached

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
    the pattern names a directory and is dropped. A pattern that starts
    with `**/` is matched at any depth, the root included; its first
    component here is `**`.
    """
    lines = [line.strip() for line in read(".dockerignore").splitlines()]
    patterns = [line for line in lines if line and not line.startswith("#")]
    for pattern in patterns:
        assert "**" not in pattern.removeprefix("**/") and not pattern.startswith("!"), (
            f"a pattern this reader does not understand: {pattern!r}"
        )
    return [pattern.strip("/").split("/") for pattern in patterns]


def matches_from(parts, pattern):
    """Whether `pattern` matches the leading components of `parts`."""
    return len(parts) >= len(pattern) and all(
        fnmatch.fnmatchcase(part, wanted) for part, wanted in zip(parts, pattern)
    )


def kept_out_of_the_image(relative, pattern):
    """Whether the build context leaves `relative` out because of `pattern`.

    It does when the pattern matches the path or a directory above it.
    """
    parts = relative.split("/")
    if pattern[0] == "**":
        return any(matches_from(parts[start:], pattern[1:]) for start in range(len(parts)))
    return matches_from(parts, pattern)


def puts_back(relative, pattern):
    """Whether mounting `relative` brings in something `pattern` keeps out.

    It does when the pattern matches the path or a directory above it, and
    when the path is a directory above what the pattern names.

    A pattern for any depth (`**/__pycache__`) names something that can be
    under every directory, so by the second rule every directory mount would
    bring it in. That is true and is not what this is asked: the mounts are
    directories of source, and what sits inside them is said to be out of
    reach in the docstring at the top. For such a pattern only the first
    rule is applied.
    """
    if relative == ".":
        return True
    if pattern[0] == "**":
        return kept_out_of_the_image(relative, pattern)
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
    for kept_out in (["**", ".env"], [".git"], ["**", "*.db"]):
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

    for source in (".", "./", "./.env", "./.git", "./.git/hooks", "./sql_app.db", "./frontend",
                   "./backend/__pycache__", "./tests/__pycache__/conftest.cpython-311.pyc"):
        assert brought_in(source), source
    for source in ("./backend", "./alembic", "./alembic.ini", "./tests"):
        assert not brought_in(source), source


# What Python and its tools leave beside the modules they touch.
BESIDE_ANY_MODULE = ("__pycache__", "*.pyc", "*.pyo", ".pytest_cache", ".ruff_cache", "*.egg-info")


def test_what_python_leaves_beside_a_module_is_kept_out_at_any_depth():
    patterns = excluded_from_the_image()
    # A pattern without `**/` is matched at the top of the build context
    # only. Written that way these kept out a cache nobody has, the one
    # beside the Dockerfile, and let in the one in every package.
    for name in BESIDE_ANY_MODULE:
        assert ["**", name] in patterns, name
        assert [name] not in patterns, name

    def kept_out(relative):
        return any(kept_out_of_the_image(relative, pattern) for pattern in patterns)

    for path in (
        "__pycache__",
        "backend/__pycache__",
        "backend/__pycache__/main.cpython-311.pyc",
        "backend/routers/__pycache__/users.cpython-310.pyc",
        "alembic/versions/__pycache__",
        "tests/__pycache__/conftest.cpython-311-pytest-8.4.2.pyc",
        "backend/stray.pyc",
        "backend/stray.pyo",
        "backend/.ruff_cache",
        "backend/.ruff_cache/0.14.0/1234",
        "tests/.pytest_cache/v/cache/lastfailed",
        "backend/vector.egg-info/PKG-INFO",
    ):
        assert kept_out(path), path
    # ... and the source beside them is not.
    for path in (
        "backend", "backend/main.py", "backend/routers/users.py", "alembic/versions",
        "alembic.ini", "requirements.txt", "tests/conftest.py", "backend/pycache.py",
    ):
        assert not kept_out(path), path


def test_the_any_depth_reader_matches_as_docker_documents():
    # `**/x` is x at the root and x below any directories; a pattern without
    # it is x at the root alone.
    assert kept_out_of_the_image("x", ["**", "x"])
    assert kept_out_of_the_image("a/b/x", ["**", "x"])
    assert kept_out_of_the_image("a/x/b", ["**", "x"])
    assert not kept_out_of_the_image("a/xy", ["**", "x"])
    assert kept_out_of_the_image("x", ["x"])
    assert kept_out_of_the_image("x/b", ["x"])
    assert not kept_out_of_the_image("a/x", ["x"])
    assert kept_out_of_the_image("a/b.pyc", ["**", "*.pyc"])
    assert not kept_out_of_the_image("a/b.py", ["**", "*.pyc"])


# --- What the image is built from (INF-M6) -----------------------------------

def image_copies():
    """Each COPY of Dockerfile.backend as (sources, destination), in order."""
    copies = []
    for keyword, arguments in read_dockerfile():
        # ADD also unpacks archives and fetches URLs. Nothing here needs it.
        assert keyword != "ADD", arguments
        if keyword == "COPY":
            words = arguments.split()
            assert not [word for word in words if word.startswith("--")], arguments
            copies.append((words[:-1], words[-1]))
    return copies


def lands_at(source, destination, working_directory="/app"):
    """Where one copied path is in the image.

    A destination that is `.` or ends in `/` is a directory the source goes
    into under its own name. Any other destination is the name it gets; for
    a directory that means its contents land there.
    """
    if destination in (".", "./") or destination.endswith("/"):
        destination = posixpath.join(destination, posixpath.basename(source))
    return posixpath.normpath(posixpath.join(working_directory, destination))


def test_the_image_copies_what_the_server_loads_by_name():
    copies = image_copies()
    # The whole list. requirements.txt first and alone, so that the install
    # after it is rebuilt only when the requirements change.
    assert copies == [
        (["requirements.txt"], "."),
        (["alembic.ini"], "./"),
        (["alembic"], "./alembic"),
        (["backend"], "./backend"),
    ]
    assert ("WORKDIR", "/app") in read_dockerfile()

    for sources, _ in copies:
        for source in sources:
            # A name, not the build context and not a pattern: `COPY . .`
            # and `COPY * .` take whatever is there on the day of the build.
            assert source not in (".", "./") and not set(source) & set("*?[]"), source
            assert not source.startswith(("/", "..")), source
            assert os.path.exists(os.path.join(PROJECT_ROOT, source)), source


def test_the_image_holds_the_paths_the_service_mounts_and_one_file_more():
    # Each mount replaces a path the image has, with the working tree's copy
    # of the same thing. A mount with no copy behind it would be a path the
    # image run by itself lacks; a copy with no mount would be source the
    # development stack runs stale. requirements.txt is the one exception:
    # it is read when the image is built and by nothing in the container.
    copied = {
        lands_at(source, destination)
        for sources, destination in image_copies() for source in sources
    }
    mounted = {item["target"] for item in backend_mounts()}

    assert copied - mounted == {"/app/requirements.txt"}
    assert mounted - copied == set()
    # ... and from the same place on the host.
    for item in backend_mounts():
        assert lands_at(in_the_repository(item["source"]), "./") == item["target"], item


def test_the_copy_reader_knows_where_a_path_lands():
    assert lands_at("requirements.txt", ".") == "/app/requirements.txt"
    assert lands_at("alembic.ini", "./") == "/app/alembic.ini"
    assert lands_at("alembic", "./alembic") == "/app/alembic"
    assert lands_at("backend", "./") == "/app/backend"
    assert lands_at("backend", "./src") == "/app/src"


def test_a_secret_or_a_database_inside_a_copied_directory_is_kept_out():
    # The directories are copied whole, so what a developer leaves inside
    # one goes with it unless .dockerignore says otherwise. backend/.env is
    # not a remote case: python-dotenv, called from backend/database.py,
    # looks beside that file before it looks in /app, so the server would
    # read it.
    patterns = excluded_from_the_image()

    def kept_out(relative):
        return any(kept_out_of_the_image(relative, pattern) for pattern in patterns)

    for path in (
        ".env",
        "backend/.env",
        "backend/.env.local",
        "backend/routers/.env",
        "alembic/.env",
        "sql_app.db",
        "backend/sql_app.db",
        "alembic/versions/scratch.db",
    ):
        assert kept_out(path), path
    for path in (
        "backend/database.py", "backend/security.py", "alembic/env.py",
        "backend/dbsession.py", "alembic.ini", "requirements.txt",
    ):
        assert not kept_out(path), path


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


PLANT_STEP = "Plant what the image must not take"
PLANTED_INSIDE = (
    "backend/__pycache__/planted.cpython-310.pyc",
    "backend/planted.pyc",
    "backend/.env",
)
PLANTED_BESIDE = "planted-beside-the-source.txt"


def test_the_ci_job_plants_what_the_image_must_not_take_before_it_is_built():
    # A checkout has no bytecode and no backend/.env, so "the image has
    # none" would be true of any .dockerignore; and it has nothing beside
    # the source that a tracked file does not already put there. The job
    # makes them first: a cache directory for `**/__pycache__/`, a bytecode
    # file outside one for `**/*.pyc`, a .env for `**/.env`, and a file
    # beside the source for the COPY lines.
    names = [step.get("name") for step in ci_job()["steps"]]
    assert names.index(PLANT_STEP) < names.index("Build and start the stack")

    assert script_lines(step_named(PLANT_STEP)) == [
        "mkdir -p backend/__pycache__",
        "echo planted > backend/__pycache__/planted.cpython-310.pyc",
        "echo planted > backend/planted.pyc",
        "echo PLANTED=1 > backend/.env",
        "echo planted > planted-beside-the-source.txt",
    ]

    patterns = excluded_from_the_image()
    copied = [source for sources, _ in image_copies() for source in sources]
    for path in PLANTED_INSIDE:
        # Inside a directory the image copies, so only .dockerignore keeps
        # it out ...
        assert path.split("/")[0] in copied, path
        assert any(kept_out_of_the_image(path, pattern) for pattern in patterns), path
        # ... and not a database file: the script's own `find /app -name
        # '*.db'` reads the container through the mounts and would see it.
        assert not path.endswith(".db")
    # Beside them, where no pattern speaks of it: only the COPY lines keep
    # this one out.
    assert PLANTED_BESIDE not in copied
    assert not any(kept_out_of_the_image(PLANTED_BESIDE, pattern) for pattern in patterns)


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
    always_run = reached(step_named(PROBE_STEP)["run"])

    for probe in (
        # the server answers before anything is concluded from the container
        'curl -fsS -o /dev/null "$api/openapi.json"',
        # what is looked for exists on the host, and exec works
        "test -f .env",
        "test -e .git",
        "test -f backend/__pycache__/planted.cpython-310.pyc",
        "test -f backend/planted.pyc",
        "test -f backend/.env",
        "test -f planted-beside-the-source.txt",
        "docker compose exec -T backend test -f /app/backend/main.py",
        # what the build context leaves out is not in the container
        "if docker compose exec -T backend test -e /app/.env; then",
        "if docker compose exec -T backend test -e /app/.git; then",
        "databases=$(docker compose exec -T backend find /app -name '*.db')",
        'test -z "$databases"',
        # INF-M6: what the image holds, read with nothing mounted over it
        "image=$(docker inspect --format '{{.Image}}' \"$container\")",
        'docker run --rm "$image" test -f /app/backend/main.py',
        'built=$(docker run --rm "$image" ls -A /app | LC_ALL=C sort)',
        "test \"$built\" = \"$(printf '%s\\n' alembic alembic.ini backend requirements.txt)\"",
        (
            "planted=$(docker run --rm \"$image\" find /app -name __pycache__ -o -name '*.pyc' "
            "-o -name '.env*' -o -name '*.db' -o -name 'planted*')"
        ),
        'test -z "$planted"',
        # the three mounts, read-only
        (
            "test \"$mounts\" = \"$(printf '%s\\n' 'bind /app/alembic rw=false' "
            "'bind /app/alembic.ini rw=false' 'bind /app/backend rw=false')\""
        ),
        "if docker compose exec -T --user 0 backend touch /app/backend/written-from-the-container; then",
        # INF-H4: who runs the processes in each container, and that the
        # backend's account cannot write beside the application
        "owners=$(docker compose exec -T backend sh -c 'stat -c %u /proc/[0-9]* 2>/dev/null; true' | sort -u)",
        "if docker compose exec -T backend touch /app/written-from-the-container; then",
        "owners=$(docker compose exec -T frontend sh -c 'stat -c %u /proc/[0-9]* 2>/dev/null; true' | sort -u)",
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
        # ... and outside every `if`, loop and `case`, where nothing can
        # step over it.
        assert probe in always_run, f"the probe script may skip: {probe}"

    # The server is asked twice outside a waiting loop: before anything is
    # concluded from the container, and again after the reload.
    assert commands.count('curl -fsS -o /dev/null "$api/openapi.json"') == 2

    # Each `if` probe ends the script when its condition holds.
    conditions = [line for line in commands if line.startswith("if docker compose exec ")]
    assert len(conditions) == 4
    # Every `if` that spans lines is one of those four: there is no other
    # block in the script for a probe to be put inside. The loops are
    # `for`, and `reached` keeps their bodies out of what always runs.
    assert [line for line in always_run if line.startswith("if ")] == conditions
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

    # The image is the one that container was made from, and is read after
    # the container is known. What `ls` must print is every path the image
    # copies, and what `find` looks for covers every plant.
    assert always_run.index("container=$(docker compose ps -q backend)") < always_run.index(
        "image=$(docker inspect --format '{{.Image}}' \"$container\")"
    )
    top = sorted(
        posixpath.relpath(lands_at(source, destination), "/app")
        for sources, destination in image_copies() for source in sources
    )
    assert f"printf '%s\\n' {' '.join(top)})" in "\n".join(always_run)
    for path in (*PLANTED_INSIDE, PLANTED_BESIDE):
        name = posixpath.basename(path)
        assert any(
            fnmatch.fnmatchcase(name, wanted) for wanted in ("*.pyc", ".env*", "*.db", "planted*")
        ), path
