"""INF-H8 -- every image is named by a tag and a digest.

The two Dockerfiles, docker-compose.yml and the CI workflow each named their
images by a tag alone: `python:3.10`, `node:24-alpine`, `nginx:alpine`,
`postgres:15-alpine`, `postgres:15`. A tag is moved by whoever publishes the
image, so the same file could build a different image from one week to the
next, and `nginx:alpine` did not say which release it was. Each is now
`name:tag@sha256:<digest>`. Docker takes the image the digest names, and
.github/dependabot.yml asks Dependabot to propose newer ones.

Nothing here needs Docker or the network; one test asks git which files are
tracked. The tests read Dockerfile.backend, frontend/Dockerfile,
docker-compose.yml, the workflows and dependabot.yml.

What is NOT covered here:
  - that a digest is the one its tag has, or had, or that it exists. Docker
    finds a digest that does not exist when it pulls: the `frontend-image`
    and `compose-stack` jobs in .github/workflows/ci.yml build both
    Dockerfiles and start the compose stack, and the `backend` job starts
    its service. Docker does not compare the tag with the digest, so a tag
    that says one release beside the digest of another passes there too;
  - an image named on a command line or in a `uses: docker://...` step. The
    `docker run` lines ci.yml has today start images the job has just built,
    and no workflow has a `docker://` step;
  - an image named somewhere these tests do not read: an action's own
    `action.yml`, a compose file brought in by `include:` or `extends:`
    under a name the pattern below does not match, a devcontainer file. The
    repository has none of them today;
  - a service that has a `build:`. Its `image:`, if it has one, is the name
    Compose gives what it builds, and is not read;
  - what Dependabot does with dependabot.yml. The last test checks that the
    file names the directories, and does not read its `ignore` entries;
    whether a pull request comes of it is seen on GitHub;
  - a Dockerfile or compose file git does not track. The test that looks for
    files nobody listed here asks git, and is skipped where there is no git
    or git cannot list the checkout's files.
"""
import os
import re
import shutil
import subprocess

import pytest
import yaml

from tests.test_container_entrypoint import dockerfile_instructions
from tests.test_frontend_image import PROJECT_ROOT, build_stages, read

# Where an image is named, and the images each file names, in order. Names
# only: a tag or a digest is expected to change, and a sixth image, or one
# swapped for another, is expected to be looked at by whoever adds it here.
DOCKERFILES = {
    "Dockerfile.backend": ["python"],
    "frontend/Dockerfile": ["node", "nginx"],
}
COMPOSE_FILES = {
    "docker-compose.yml": ["postgres"],
}
WORKFLOWS = {
    ".github/workflows/ci.yml": ["postgres"],
}

# `name:tag@sha256:<digest>`. The tag starts with a digit, so it names a
# version, if only a major or a series (`15`, `3.10`); `latest`, `alpine`,
# `stable` and `slim` name none. A tag written `v1.2.3` is refused as well,
# and the first image tagged that way means widening this. The digest is
# sha256's 64 hexadecimal digits, in lower case as Docker prints them.
# Nothing else is allowed in the reference, a `$` included, so a variable
# cannot stand in for any part of it.
PINNED = re.compile(
    r"(?P<name>[a-z0-9]+(?:[._/-][a-z0-9]+)*)"
    r":(?P<tag>[0-9][A-Za-z0-9_.-]*)"
    r"@sha256:(?P<digest>[0-9a-f]{64})"
)

# The names Dependabot's two readers take, after dependabot-core on
# 2026-10-12: `docker` reads a file whose name contains "dockerfile" or
# "containerfile", `docker-compose` one that matches the second pattern
# (written there with an atomic group, which Python 3.10 does not have).
DOCKERFILE_NAME = re.compile(r"dockerfile|containerfile", re.IGNORECASE)
COMPOSE_NAME = re.compile(r"(docker-)?compose(-[\w]+)?(\.[\w-]+)?\.ya?ml", re.IGNORECASE)


def pinned(reference):
    """Return (name, tag, digest) of an image reference, or fail."""
    found = PINNED.fullmatch(reference)
    assert found, (
        f"`{reference}` is not `name:tag@sha256:<64 hex digits>` with a tag "
        "that starts with a version number"
    )
    return found.group("name", "tag", "digest")


# --- Reading the files -------------------------------------------------------

def images_from_elsewhere(text):
    """What a Dockerfile's FROM lines name, less its own stages and `scratch`.

    A stage name counts only from the FROM that gives it: Docker reads a FROM
    that names a later stage, or its own, as an image.
    """
    own = set()
    images = []
    for stage in build_stages(text):
        if stage["base"] not in own and stage["base"] != "scratch":
            images.append(stage["base"])
        if stage["name"]:
            own.add(stage["name"])
    return images


def named_outside_from(text):
    """What a Dockerfile's other instructions take from outside the build.

    `COPY --from=<x>` and `RUN --mount=...,from=<x>` each take files from a
    stage or, when no stage has that name, from an image. This returns each
    <x> that is not an earlier stage's name or number. A `from=` anywhere
    else in a RUN line is that command's own text. ADD is not read: Docker
    refuses a `--from` on it (seen 2026-10-12).
    """
    earlier = set()
    found = []
    for number, stage in enumerate(build_stages(text)):
        for keyword, arguments in stage["steps"]:
            if keyword == "COPY":
                sources = re.findall(r"--from=(\S+)", arguments)
            elif keyword == "RUN":
                sources = re.findall(r"--mount=\S*?\bfrom=([^,\s]+)", arguments)
            else:
                sources = []
            found += [source for source in sources if source not in earlier]
        earlier.add(str(number))
        if stage["name"]:
            earlier.add(stage["name"])
    return found


def compose_images(text):
    """The `image:` of each compose service that is not built, in order.

    With a `build:`, an `image:` is the name Compose gives the result.
    """
    services = yaml.safe_load(text)["services"]
    return [
        service["image"] for service in services.values()
        if "image" in service and "build" not in service
    ]


def workflow_images(text):
    """Every image a workflow's jobs name: each service's, and a job's own
    `container:`, which is written as a string or as a mapping."""
    images = []
    for job in yaml.safe_load(text)["jobs"].values():
        container = job.get("container")
        if isinstance(container, str):
            images.append(container)
        elif container:
            images.append(container["image"])
        for service in (job.get("services") or {}).values():
            images.append(service["image"])
    return images


def every_reference():
    """(file, reference) for each image named in the files listed above."""
    found = []
    for path in DOCKERFILES:
        found += [(path, image) for image in images_from_elsewhere(read(path))]
    for path in COMPOSE_FILES:
        found += [(path, image) for image in compose_images(read(path))]
    for path in WORKFLOWS:
        found += [(path, image) for image in workflow_images(read(path))]
    return found


def tracked_files():
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=PROJECT_ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode == 128:
        pytest.skip(f"git could not list the files: {result.stderr.strip()}")
    # Any other failure would list nothing, and nothing listed reads as
    # nothing to find.
    assert result.returncode == 0, result.stderr
    return [path for path in result.stdout.split("\0") if path]


# --- The readers themselves --------------------------------------------------

DIGEST = "0123456789abcdef" * 4


def test_the_dockerfile_reader_tells_an_image_from_a_stage():
    text = "\n".join([
        "FROM --platform=linux/amd64 node:24 AS dev",
        "FROM dev AS build",
        "FROM scratch AS empty",
        "FROM later",
        "FROM nginx:1 AS later",
        "FROM build",
    ])
    # `later` on the fourth line is an image: no stage has that name yet.
    assert images_from_elsewhere(text) == ["node:24", "later", "nginx:1"]


def test_the_dockerfile_reader_finds_what_other_instructions_take_from_an_image():
    text = "\n".join([
        "FROM node:24 AS dev",
        "COPY --from=later /a /a",
        "RUN --mount=type=bind,from=tools:1,target=/t make from=source",
        "FROM nginx:1 AS later",
        "COPY --chown=0:0 --from=dev /b /b",
        "COPY --from=0 /c /c",
        "COPY --from=1 /d /d",
        "COPY --from=busybox /e /e",
        "RUN --mount=type=cache,target=/c --mount=from=dev,target=/s true",
    ])
    # `later` on the second line and `1` on the seventh are not earlier
    # stages where they are read, so both are reported.
    assert named_outside_from(text) == ["later", "tools:1", "1", "busybox"]


def test_the_compose_reader_finds_each_image_that_is_not_built():
    text = "\n".join([
        "services:",
        "  one:",
        "    image: a:1",
        "  two:",
        "    build: .",
        "  three:",
        "    build: .",
        "    image: the-name-of-what-is-built",
        "  four:",
        "    image: b:2",
    ])
    assert compose_images(text) == ["a:1", "b:2"]


def test_the_workflow_reader_finds_services_and_both_forms_of_container():
    text = "\n".join([
        "on: push",
        "jobs:",
        "  one:",
        "    container: a:1",
        "    services:",
        "      db:",
        "        image: b:2",
        "      cache:",
        "        image: c:3",
        "  two:",
        "    container:",
        "      image: d:4",
        "  three:",
        "    steps: []",
    ])
    assert workflow_images(text) == ["a:1", "b:2", "c:3", "d:4"]


def test_a_pinned_reference_is_read_into_its_three_parts():
    assert pinned(f"python:3.10@sha256:{DIGEST}") == ("python", "3.10", DIGEST)
    assert pinned(f"ghcr.io/org/tool:1.2.3-alpine@sha256:{DIGEST}") == (
        "ghcr.io/org/tool", "1.2.3-alpine", DIGEST,
    )


@pytest.mark.parametrize("reference", [
    "python:3.10",                              # the tag alone
    f"python@sha256:{DIGEST}",                  # the digest alone
    "python",                                   # neither
    f"python:latest@sha256:{DIGEST}",           # a tag that names no release
    f"nginx:alpine@sha256:{DIGEST}",
    f"python:3.10-slim@sha256:{DIGEST[:-1]}",   # a digit short
    f"python:3.10@sha256:{DIGEST}0",            # a digit over
    f"python:3.10@sha256:{DIGEST.upper()}",     # not as Docker prints it
    f"python:3.10@sha512:{DIGEST}",             # another algorithm's name
    f"python:3.10@sha256:{'g' * 64}",           # not hexadecimal
    f"python:${{TAG}}@sha256:{DIGEST}",         # a variable for the tag
    f"$BASE:3.10@sha256:{DIGEST}",              # a variable for the name
    "${BASE_IMAGE}",                            # a variable for all of it
    f"python:3.10@sha256:{DIGEST} ",            # anything after it
])
def test_anything_short_of_a_tag_and_a_whole_digest_is_refused(reference):
    with pytest.raises(AssertionError):
        pinned(reference)


# --- The images --------------------------------------------------------------

def test_every_image_is_named_by_a_tag_and_a_digest():
    references = every_reference()
    # With none found, the loop below would check nothing. Which images each
    # file should name is the next test's to say.
    assert references
    for path, reference in references:
        try:
            pinned(reference)
        except AssertionError as error:
            raise AssertionError(f"{path}: {error}") from None


@pytest.mark.parametrize("path, read_images", [
    *[(path, images_from_elsewhere) for path in DOCKERFILES],
    *[(path, compose_images) for path in COMPOSE_FILES],
    *[(path, workflow_images) for path in WORKFLOWS],
])
def test_each_file_names_the_images_listed_for_it_and_no_other(path, read_images):
    expected = {**DOCKERFILES, **COMPOSE_FILES, **WORKFLOWS}[path]
    # Split at the first `:` or `@`, so that this still names the image when
    # it has lost its tag or its digest; the test above says which.
    assert [re.split(r"[:@]", image)[0] for image in read_images(read(path))] == expected


@pytest.mark.parametrize("path", DOCKERFILES)
def test_no_dockerfile_declares_a_variable_for_its_from_lines(path):
    # An ARG is the one instruction Docker allows before the first FROM, and
    # a variable for a FROM line is what one there is for. A `$` in the image
    # itself is refused above, by the pattern. One in a flag is not looked
    # for: `--platform=$BUILDPLATFORM` chooses among the builds a digest
    # lists and does not change the digest.
    assert dockerfile_instructions(read(path))[0][0] == "FROM", (
        f"{path} has an instruction before its first FROM"
    )


@pytest.mark.parametrize("path", DOCKERFILES)
def test_a_dockerfile_takes_nothing_from_an_image_but_in_its_from_lines(path):
    text = read(path)

    # Files copied or mounted from an image that no FROM line names.
    assert named_outside_from(text) == []
    # `# syntax=<image>` makes Docker fetch that image to read the file with.
    # The readers here drop it with the comments, so it is looked for by
    # itself.
    assert not re.search(r"^\s*#\s*syntax\s*=", text, re.MULTILINE | re.IGNORECASE)


def test_no_other_file_that_names_images_goes_unread():
    tracked = tracked_files()

    def named(pattern):
        return sorted(path for path in tracked if pattern.search(path.rsplit("/", 1)[-1]))

    workflows = sorted(
        path for path in tracked
        if path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml"))
    )

    assert named(DOCKERFILE_NAME) == sorted(DOCKERFILES)
    assert named(COMPOSE_NAME) == sorted(COMPOSE_FILES)
    assert workflows == sorted(WORKFLOWS)


# --- Dependabot ---------------------------------------------------------------

def directory_of(path):
    """A file's directory as dependabot.yml writes one: `/`, `/frontend`."""
    return "/" + os.path.dirname(path)


def test_dependabot_is_asked_about_each_directory_that_holds_a_pin():
    config = yaml.safe_load(read(".github", "dependabot.yml"))
    assert config["version"] == 2

    asked = {}
    for entry in config["updates"]:
        # `directory` for one, `directories` for several; `frontend`,
        # `/frontend` and `/frontend/` are read as the same place.
        directories = entry.get("directories") or [entry["directory"]]
        asked.setdefault(entry["package-ecosystem"], set()).update(
            "/" + directory.strip("/") for directory in directories
        )
        # Which interval is the entry's own business; that it has one is
        # what makes it a schedule.
        assert entry["schedule"]["interval"]

    assert asked.get("docker") == {directory_of(path) for path in DOCKERFILES}
    assert asked.get("docker-compose") == {directory_of(path) for path in COMPOSE_FILES}
    # The workflow's image has no entry to look for: dependabot.yml says why.
