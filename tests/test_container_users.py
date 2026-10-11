"""INF-H4 -- neither image this repository builds runs as root.

Dockerfile.backend and frontend/Dockerfile used to declare no user, so
uvicorn, Vite's development server and nginx's master process each ran as
uid 0. Each stage now names an unprivileged account before anything in it is
started: Dockerfile.backend makes one with a fixed number, the `dev` stage
(and the `build` stage that continues it) uses the node image's `node`, and
the last stage uses the nginx image's `nginx`, by its number.

Nothing here needs Docker. The tests read the two Dockerfiles,
docker-compose.yml and the CI workflow.

What is NOT covered here:
  - who the processes in a running container belong to. A Dockerfile is
    text; an entrypoint in a base image could start something as another
    account, and `node` is a name whose number the base image decides. The
    `frontend-image` and `compose-stack` jobs in
    .github/workflows/ci.yml start the containers and read the owner of every
    process in them, and are the only check of that. These tests do not run
    those jobs. They check that the probe lines are still in the scripts
    (tests/test_frontend_image.py and tests/test_backend_mounts.py hold the
    lines themselves) and what number each is compared with;
  - the backend image run with its own command. The CI job reads the
    container docker-compose.yml starts, whose command is the compose file's;
  - what the nginx RUN line does to the image. It is compared as text here.
    The build fails if its `test` finds the `nginx` account under another
    number or its `grep` finds no rewritten `pid` line, and the
    `frontend-image` job is where that build happens;
  - a probe that is in the script and does something else. Each line is
    looked for outside every `if`, loop and `case`, and a script that leaves
    early or redefines a command in one of the ways `reached`
    (tests/test_frontend_image.py) lists is refused. Another way of making
    `docker` or `test` mean something else is not seen;
  - a Dockerfile written in a way the reader does not follow. A heredoc and
    a parser directive are refused outright; a variable in a USER line fails
    the exact comparisons below;
  - the `db` service. Its image is postgres's own, which starts as root and
    hands over to its `postgres` account; this repository builds no image
    for it;
  - `docker run --user 0`, `docker exec --user 0`, and anything else said
    by whoever starts a container. An image cannot refuse it;
  - what an unprivileged process can still do: no capability is dropped and
    no service sets `no-new-privileges` or a read-only root filesystem.

PyYAML is imported here and is not named in requirements.txt; it arrives
through `uvicorn[standard]`.
"""
import re
import shlex

import pytest
import yaml

from tests import test_backend_mounts, test_frontend_image
from tests.test_container_entrypoint import read_dockerfile
from tests.test_frontend_image import build_stages, image_stages, reached, read

ROOT_NAME = "root"

# The account Dockerfile.backend makes.
BACKEND_ACCOUNT = "app"
BACKEND_UID = "10001"
# The accounts the base images ship, and the numbers the CI probes expect
# them to have.
NODE_ACCOUNT, NODE_UID = "node", "1000"
NGINX_ACCOUNT, NGINX_UID = "nginx", "101"

# Every key of the frontend service in docker-compose.yml.
FRONTEND_SERVICE_KEYS = {"build", "ports", "depends_on", "environment", "volumes", "command"}

OWNERS_OF_EVERY_PROCESS = "sh -c 'stat -c %u /proc/[0-9]* 2>/dev/null; true' | sort -u)"


# --- Reading who a stage runs as ---------------------------------------------

def account(user):
    """The account part of a USER argument: `node:node` -> `node`."""
    return user.split(":")[0]


def is_root(user):
    """Whether a USER argument names root: by name, or by any spelling of 0."""
    who = account(user).strip().lower()
    if who == ROOT_NAME:
        return True
    try:
        return int(who) == 0
    except ValueError:
        return False


def users_by_stage(stages):
    """For each stage, every user it has in turn, in order.

    A stage that continues an earlier one (`FROM dev AS build`) starts as
    that stage ended. A stage built on an image from elsewhere starts as
    None: this reader cannot see into that image, so it does not guess. Each
    USER instruction then adds one. The last entry is who the stage's
    command, or a later stage built on it, runs as.
    """
    ended_as = {}
    result = []
    for stage in stages:
        users = [ended_as.get(stage["base"])]
        users += [arguments for keyword, arguments in stage["steps"] if keyword == "USER"]
        if stage["name"]:
            ended_as[stage["name"]] = users[-1]
        result.append(users)
    return result


def backend_stages():
    return build_stages(read("Dockerfile.backend"))


def every_stage():
    """(label, users) for each stage of both Dockerfiles."""
    labelled = []
    for path, stages in (
        ("Dockerfile.backend", backend_stages()),
        ("frontend/Dockerfile", image_stages()),
    ):
        for stage, users in zip(stages, users_by_stage(stages)):
            labelled.append((f"{path}, stage {stage['name'] or stage['base']}", users))
    return labelled


def steps_before_the_first_user(stage):
    keywords = [keyword for keyword, _ in stage["steps"]]
    return stage["steps"][:keywords.index("USER")]


def test_the_reader_follows_a_stage_into_the_one_built_on_it():
    stages = build_stages("""
        FROM node:24 AS one
        RUN true
        USER node
        FROM one AS two
        RUN true
        FROM two
        USER root
        USER 1000:1000
        FROM nginx
    """)

    assert users_by_stage(stages) == [
        [None, "node"],
        ["node"],
        ["node", "root", "1000:1000"],
        [None],
    ]


@pytest.mark.parametrize("user, root", [
    ("root", True),
    ("0", True),
    ("0:0", True),
    ("root:root", True),
    ("ROOT", True),
    ("0:10001", True),
    ("00", True),
    ("+0", True),
    ("000:5", True),
    ("node", False),
    ("10001:10001", False),
    # The group is not what this asks about.
    ("10001:0", False),
    ("101:101", False),
    # A name, and not this one.
    ("rooter", False),
])
def test_the_reader_knows_root_by_name_and_by_number(user, root):
    assert is_root(user) is root


@pytest.mark.parametrize("path", ["Dockerfile.backend", "frontend/Dockerfile"])
def test_neither_file_is_written_in_a_way_the_reader_does_not_follow(path):
    # dockerfile_instructions says what it does not handle. A heredoc would
    # put lines in a RUN that it reads as instructions of their own, and a
    # parser directive (`# escape=`, `# syntax=`) can change what a line means.
    text = read(*path.split("/"))
    assert "<<" not in text
    for line in text.splitlines():
        assert not re.match(r"#\s*(escape|syntax|check)\s*=", line.strip(), re.IGNORECASE), line


# --- Who each stage runs as --------------------------------------------------

def test_every_stage_ends_as_an_account_that_is_not_root():
    stages = every_stage()
    # One stage in the backend's file, three in the frontend's.
    assert len(stages) == 4

    for label, users in stages:
        final = users[-1]
        assert final is not None, f"{label} names no user, so it runs as its base image's"
        assert not is_root(final), f"{label} runs as {final}"


def test_no_stage_goes_back_to_root_once_it_has_left():
    # Stricter than the test above, which reads only where a stage ends:
    # `USER root` followed later by `USER node` ends well and still runs
    # whatever is between them as root.
    for label, users in every_stage():
        named = [user for user in users if user is not None]
        assert not [user for user in named if is_root(user)], f"{label}: {named}"


def test_each_stage_names_its_user_once():
    assert [users for _, users in every_stage()] == [
        [None, f"{BACKEND_UID}:{BACKEND_UID}"],
        [None, NODE_ACCOUNT],
        # The build continues the stage above and says nothing of its own.
        [NODE_ACCOUNT],
        [None, f"{NGINX_UID}:{NGINX_UID}"],
    ]


# --- The backend image -------------------------------------------------------

def test_the_backend_user_is_a_number_and_the_account_made_for_it_has_that_number():
    instructions = read_dockerfile()
    users = [arguments for keyword, arguments in instructions if keyword == "USER"]
    assert users == [f"{BACKEND_UID}:{BACKEND_UID}"]
    # A number, so that it can be checked without the image's /etc/passwd,
    # and one far from those a distribution hands out itself.
    assert BACKEND_UID.isdigit() and int(BACKEND_UID) >= 10000

    position = instructions.index(("USER", users[0]))
    keyword, creating = instructions[position - 1]
    assert keyword == "RUN"
    commands = [shlex.split(command) for command in creating.split("&&")]
    assert [command[0] for command in commands] == ["groupadd", "useradd"]
    groupadd, useradd = commands

    assert groupadd[groupadd.index("--gid") + 1] == BACKEND_UID
    assert groupadd[-1] == BACKEND_ACCOUNT
    assert useradd[useradd.index("--uid") + 1] == BACKEND_UID
    assert useradd[useradd.index("--gid") + 1] == BACKEND_ACCOUNT
    assert useradd[-1] == BACKEND_ACCOUNT
    # No password is given, and the shell is one that refuses a login.
    assert useradd[useradd.index("--shell") + 1] == "/usr/sbin/nologin"
    for command in commands:
        assert not {"-o", "--non-unique", "-p", "--password", "-G", "--groups"} & set(command)


def test_the_backend_account_owns_nothing_the_image_was_built_with():
    instructions = read_dockerfile()
    keywords = [keyword for keyword, _ in instructions]

    # Everything that puts files in the image comes before USER, so it is
    # root's, and the server's account can read it and not change it ...
    after = instructions[keywords.index("USER") + 1:]
    assert [keyword for keyword, _ in after] == ["CMD"]
    # ... and nothing hands any of it over.
    for keyword, arguments in instructions:
        assert "--chown" not in arguments, (keyword, arguments)
        assert "--chmod" not in arguments, (keyword, arguments)
        if keyword == "RUN":
            assert not re.search(r"\b(chown|chmod|chgrp|setcap|sudo)\b", arguments), arguments


# --- The frontend's stages ---------------------------------------------------

def test_the_node_stage_changes_user_before_it_does_anything_else():
    dev = test_frontend_image.stage_named("dev")
    # Nothing is installed, copied or created as root: USER is the stage's
    # first instruction, and /app is made after it.
    assert steps_before_the_first_user(dev) == []
    assert dev["steps"][1] == ("WORKDIR", "/app")


def test_what_the_node_stage_copies_stays_roots():
    # A COPY without --chown writes its files as root's. The install, the
    # build and the development server read them; what they write goes into
    # directories they make themselves under /app (node_modules, dist).
    for name in ("dev", "build"):
        for keyword, arguments in test_frontend_image.stage_named(name)["steps"]:
            assert "--chown" not in arguments and "--chmod" not in arguments, (keyword, arguments)
            if keyword == "RUN":
                assert not re.search(r"\b(chown|chmod|chgrp|setcap|sudo|su)\b", arguments), arguments


def test_the_nginx_stage_prepares_as_root_only_what_its_user_needs():
    image = test_frontend_image.the_image()
    before = steps_before_the_first_user(image)
    runs = [arguments for keyword, arguments in before if keyword == "RUN"]
    # The one RUN in the stage, and it comes before USER: it changes files
    # only root can change.
    assert [keyword for keyword, _ in image["steps"]].count("RUN") == 1
    assert len(runs) == 1

    commands = [shlex.split(command) for command in runs[0].split("&&")]
    assert commands == [
        # The account the image ships still has the number USER gives. The
        # build stops here if it has not.
        ["test", "$(id -u nginx):$(id -g nginx)", "=", f"{NGINX_UID}:{NGINX_UID}"],
        # nginx.conf without its `user` line, and with the pid file where
        # any account can write one.
        ["sed", "-i", "-e", "/^user /d", "-e", "s|^pid .*|pid /tmp/nginx.pid;|",
         "/etc/nginx/nginx.conf"],
        # The build stops here if the line above rewrote nothing.
        ["grep", "-qx", "pid /tmp/nginx.pid;", "/etc/nginx/nginx.conf"],
        # The one directory handed over, and not its parents or the files
        # nginx serves.
        ["chown", f"{NGINX_UID}:{NGINX_UID}", "/var/cache/nginx"],
    ]
    assert NGINX_ACCOUNT in commands[0][1]

    # The files nginx reads are root's. The configuration comes from the
    # build context and is root's without a flag. The built files come from a
    # stage that made them as `node`, and a COPY from a stage keeps the owner
    # unless told otherwise, so that one says root by number.
    copies = [arguments.split() for keyword, arguments in image["steps"] if keyword == "COPY"]
    assert [[word for word in copy if word.startswith("--")] for copy in copies] == [
        [],
        ["--chown=0:0", "--from=build"],
    ]


# --- The development stack ---------------------------------------------------

PUTS_ROOT_BACK = ("user", "privileged", "cap_add", "userns_mode", "pid", "group_add")


@pytest.mark.parametrize("service", ["backend", "frontend"])
def test_the_compose_file_does_not_say_otherwise(service):
    # `user:` replaces the image's USER, and the others give a process that
    # is not root some of what root has. None is needed, so none is allowed:
    # the two services run as their images say.
    described = yaml.safe_load(read("docker-compose.yml"))["services"][service]
    for key in PUTS_ROOT_BACK:
        assert key not in described, f"the {service} service sets `{key}`"


def test_the_frontend_service_has_no_key_this_file_has_not_read():
    # The test above looks for keys by name in this one file. `extends` would
    # bring a `user:` in from another file, and there are more keys than the
    # list above knows. The whole key set, then, as
    # tests/test_backend_mounts.py holds the backend's.
    services = yaml.safe_load(read("docker-compose.yml"))["services"]
    assert set(services["frontend"]) == FRONTEND_SERVICE_KEYS
    assert set(services["frontend"]["build"]) == {"context", "dockerfile", "target"}
    assert set(services["backend"]["build"]) == {"context", "dockerfile"}


def test_there_are_two_services_built_here_and_one_that_is_not():
    # The parametrised test above names its services. This fails when a
    # fourth one appears, or one of the two stops being built from a file in
    # this repository, so that the list is looked at again.
    services = yaml.safe_load(read("docker-compose.yml"))["services"]
    assert {name for name, described in services.items() if "build" in described} == {
        "backend", "frontend",
    }
    assert set(services) == {"db", "backend", "frontend"}


# --- The CI probes -----------------------------------------------------------

def expected_owner(commands, listing):
    """What the script compares a process listing with, straight after it."""
    position = commands.index(listing)
    assert commands[position + 1] == 'echo "$owners"'
    comparison = shlex.split(commands[position + 2])
    assert comparison[:3] == ["test", "$owners", "="], comparison
    assert len(comparison) == 4
    return comparison[3]


def test_the_frontend_image_job_expects_every_process_to_be_nginxs():
    script = test_frontend_image.step_running("docker run")["run"]
    commands = [line.strip() for line in script.splitlines()]

    listing = f"owners=$(docker exec frontend-image {OWNERS_OF_EVERY_PROCESS}"
    assert commands.count(listing) == 1
    assert expected_owner(commands, listing) == NGINX_UID


def test_the_frontend_image_job_expects_the_served_files_to_be_roots():
    script = test_frontend_image.step_running("docker run")["run"]
    commands = [line.strip() for line in script.splitlines()]

    # Everything under the served directory that is not root's, which must
    # come to nothing ...
    listing = 'served=$(docker exec frontend-image find "$html" ! -user 0)'
    assert commands.count(listing) == 1
    assert commands[commands.index(listing) + 1] == 'test -z "$served"'
    assert f"html={test_frontend_image.SERVED_FROM}" in commands

    # ... and a write there by the image's own account, which must fail.
    attempt = 'if docker exec frontend-image touch "$html/written-from-the-container"; then'
    assert commands.count(attempt) == 1
    assert commands[commands.index(attempt) + 1].endswith("; exit 1")
    assert commands[commands.index(attempt) + 2] == "fi"


@pytest.mark.parametrize("service, uid", [("backend", BACKEND_UID), ("frontend", NODE_UID)])
def test_the_compose_stack_job_expects_every_process_to_be_the_services_account(service, uid):
    step = test_backend_mounts.step_named(test_backend_mounts.PROBE_STEP)
    commands = [line.strip() for line in step["run"].splitlines()]

    listing = f"owners=$(docker compose exec -T {service} {OWNERS_OF_EVERY_PROCESS}"
    assert commands.count(listing) == 1
    assert expected_owner(commands, listing) == uid


def probe_scripts():
    return {
        "frontend-image": test_frontend_image.step_running("docker run")["run"],
        "compose-stack": test_backend_mounts.step_named(test_backend_mounts.PROBE_STEP)["run"],
    }


def test_every_probe_of_this_ticket_is_a_line_that_always_runs():
    # In the script is not enough: `if false; then` above a probe and `fi`
    # below it leave the line where it was. `reached` gives the lines
    # outside every block, and refuses a script that ends early and green.
    always_run = {job: reached(script) for job, script in probe_scripts().items()}

    for job, lines in {
        "frontend-image": [
            f"owners=$(docker exec frontend-image {OWNERS_OF_EVERY_PROCESS}",
            'echo "$owners"',
            f'test "$owners" = {NGINX_UID}',
            'served=$(docker exec frontend-image find "$html" ! -user 0)',
            'test -z "$served"',
            'if docker exec frontend-image touch "$html/written-from-the-container"; then',
        ],
        "compose-stack": [
            f"owners=$(docker compose exec -T backend {OWNERS_OF_EVERY_PROCESS}",
            f'test "$owners" = {BACKEND_UID}',
            "if docker compose exec -T backend touch /app/written-from-the-container; then",
            f"owners=$(docker compose exec -T frontend {OWNERS_OF_EVERY_PROCESS}",
            f'test "$owners" = {NODE_UID}',
        ],
    }.items():
        for line in lines:
            assert line in always_run[job], f"{job} may skip: {line}"


def test_the_process_listing_survives_a_process_that_has_just_ended():
    # /proc is listed and then each entry is looked at. A process that ends
    # in between is an error to `stat`, and under `pipefail` that error
    # failed the job with every remaining owner the right one. The errors
    # are dropped and the shell ends well, so only what was read is
    # compared; with nothing read the comparison with a number fails.
    assert OWNERS_OF_EVERY_PROCESS == "sh -c 'stat -c %u /proc/[0-9]* 2>/dev/null; true' | sort -u)"
    inside = shlex.split(OWNERS_OF_EVERY_PROCESS.split("|")[0])
    assert inside[:2] == ["sh", "-c"]
    listing, _, ending = inside[2].partition(";")
    assert shlex.split(listing) == ["stat", "-c", "%u", "/proc/[0-9]*", "2>/dev/null"]
    assert ending.strip() == "true"


def test_no_probe_compares_with_root():
    # Each listing is compared with one of these numbers. This says none of
    # them is 0, whatever the constants above are changed to.
    for uid in (BACKEND_UID, NODE_UID, NGINX_UID):
        assert int(uid) > 0


def test_the_compose_stack_job_tries_to_write_beside_the_application():
    step = test_backend_mounts.step_named(test_backend_mounts.PROBE_STEP)
    commands = [line.strip() for line in step["run"].splitlines()]

    attempt = "if docker compose exec -T backend touch /app/written-from-the-container; then"
    assert commands.count(attempt) == 1
    # No --user: the command runs as the account the image names. And /app
    # itself, which no mount covers: the mounted paths under it are read-only
    # to any account (INF-H3), so a write there would show nothing about
    # this one. That probe is made as root, for the opposite reason.
    mounted_write = (
        "if docker compose exec -T --user 0 backend touch "
        "/app/backend/written-from-the-container; then"
    )
    assert commands.count(mounted_write) == 1
    mounted = [entry.split(":")[1] for entry in
               yaml.safe_load(read("docker-compose.yml"))["services"]["backend"]["volumes"]]
    assert "/app" not in mounted
    assert commands[commands.index(attempt) + 1].endswith("; exit 1")
    assert commands[commands.index(attempt) + 2] == "fi"
