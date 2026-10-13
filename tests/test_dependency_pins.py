"""INF-H5 -- the backend's packages are declared, locked and installed by hash.

requirements.txt used to list its packages with no version on any line, so the
image and CI each installed whatever was newest on the day. Four files replace
it:

  - requirements.in      the server's packages, one version each (by hand);
  - requirements.txt     compiled from it: every package, with hashes;
  - requirements-dev.in  pytest, ruff and what the tests import (by hand);
  - requirements-dev.txt compiled from it.

Dockerfile.backend installs the first lock and CI installs both, each with
--require-hashes.

Nothing here needs a network. The tests read the four files,
Dockerfile.backend, docker-compose.yml, the CI workflow and the Python sources
under backend/ and alembic/, and one of them reads what is installed in the
interpreter running the suite.

Not seen from here:
  - whether a lock is what its .in file compiles to today. The tests compare
    names and versions between the two and nothing runs the compiler, so a
    line taken out of a .in file, an extra dropped from one, or one hash
    taken off an entry that keeps another, leaves every test here green;
  - an install made some other way: a `pip` reached through a variable or a
    script file, another installer, a PIP_* environment variable;
  - the base image of Dockerfile.backend, which is named by digest and is
    tests/test_image_pins.py's to check, and pip itself. The image's pip is
    the one that base image carries; CI's is upgraded by name and not locked.
"""

import ast
import json
import re
import shlex
from importlib import metadata
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"

RUNTIME_IN, RUNTIME_LOCK = "requirements.in", "requirements.txt"
DEV_IN, DEV_LOCK = "requirements-dev.in", "requirements-dev.txt"
PAIRS = [(RUNTIME_IN, RUNTIME_LOCK), (DEV_IN, DEV_LOCK)]
LOCKS = [RUNTIME_LOCK, DEV_LOCK]

# The only line of a .in file that is not a package: the development file
# holds itself to the versions the runtime lock has.
CONSTRAINT_LINE = f"-c {RUNTIME_LOCK}"

# name, optional extras, `==`, a version, and nothing after it.
DECLARED = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,_-]+\])?==[0-9][A-Za-z0-9.!+]*")
HASH = re.compile(r"--hash=sha256:[0-9a-f]{64}")

# The one install that names a package: CI brings pip itself up to date before
# it installs the locks.
UPGRADE_PIP = ["--upgrade", "pip"]
# What else an install line may carry besides `-r <lock>`.
INSTALL_OPTIONS = {"--require-hashes", "--no-cache-dir"}


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


# --- Reading a .in file -------------------------------------------------------

def declared_lines(text):
    """The lines of a .in file that are not blank and not a comment."""
    lines = (line.split("#", 1)[0].strip() for line in text.splitlines())
    return [line for line in lines if line]


def declared(text):
    """{name: version} for a .in file, the constraint line left out."""
    found = {}
    for line in declared_lines(text):
        if line == CONSTRAINT_LINE:
            continue
        requirement = Requirement(line)
        found[canonicalize_name(requirement.name)] = str(requirement.specifier)[2:]
    return found


def test_the_in_reader_sees_each_form():
    text = "# a note\n\n-c requirements.txt\nFastAPI==1.2.3  # why\nuvicorn[standard]==0.5\n"
    assert declared_lines(text) == ["-c requirements.txt", "FastAPI==1.2.3", "uvicorn[standard]==0.5"]
    assert declared(text) == {"fastapi": "1.2.3", "uvicorn": "0.5"}


@pytest.mark.parametrize("line", ["fastapi==0.143.0", "uvicorn[standard]==0.54.0", "python-jose[cryptography]==3.5.0"])
def test_the_declared_form_accepts(line):
    assert DECLARED.fullmatch(line)


@pytest.mark.parametrize("line", [
    "fastapi", "fastapi>=0.143.0", "fastapi~=0.143.0", "fastapi==0.143.*", "fastapi==0.143.0,<1",
    "fastapi==0.143.0 ; python_version > '3'", "fastapi @ https://example.invalid/f.whl",
    "-r other.txt", "-e .", "--index-url https://example.invalid",
])
def test_the_declared_form_refuses(line):
    assert not DECLARED.fullmatch(line)


# --- Reading a lock -----------------------------------------------------------

def locked(text):
    """Each entry of a lock: (name, version or None, marker or None, hashes).

    An entry is one requirement and the `--hash` options on the lines that
    continue it. The version is None unless the entry holds exactly one
    `==` version.
    """
    entries = []
    for line in text.replace("\\\n", " ").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        head = re.split(r"\s--hash=", line, maxsplit=1)[0].strip()
        requirement = Requirement(head)
        specifiers = list(requirement.specifier)
        exact = len(specifiers) == 1 and specifiers[0].operator == "==" and "*" not in specifiers[0].version
        entries.append((
            canonicalize_name(requirement.name),
            specifiers[0].version if exact else None,
            requirement.marker,
            HASH.findall(line),
        ))
    return entries


def versions(text):
    return {name: version for name, version, _, _ in locked(text)}


def test_the_lock_reader_sees_each_form():
    text = (
        "# made by a tool\n"
        "Fast_API==1.2.3 \\\n    --hash=sha256:" + "a" * 64 + " \\\n    --hash=sha256:" + "b" * 64 + "\n"
        "    # via -r requirements.in\n"
        "colorama==0.4.6 ; sys_platform == 'win32' \\\n    --hash=sha256:" + "c" * 64 + "\n"
        "loose>=1\n"
        "short==1.0 \\\n    --hash=sha256:abc\n"
    )
    read_back = locked(text)
    assert [(name, version, len(hashes)) for name, version, _, hashes in read_back] == [
        ("fast-api", "1.2.3", 2), ("colorama", "0.4.6", 1), ("loose", None, 0), ("short", "1.0", 0),
    ]
    assert [str(marker) if marker else None for _, _, marker, _ in read_back] == [
        None, 'sys_platform == "win32"', None, None,
    ]


# --- The .in files ------------------------------------------------------------

@pytest.mark.parametrize("name", [RUNTIME_IN, DEV_IN])
def test_every_declared_package_has_one_exact_version(name):
    lines = [line for line in declared_lines(read(name)) if line != CONSTRAINT_LINE]
    assert lines
    assert [line for line in lines if not DECLARED.fullmatch(line)] == []


@pytest.mark.parametrize("name", [RUNTIME_IN, DEV_IN])
def test_no_package_is_declared_twice(name):
    lines = [line for line in declared_lines(read(name)) if line != CONSTRAINT_LINE]
    names = [canonicalize_name(Requirement(line).name) for line in lines]
    assert sorted(names) == sorted(set(names))


def test_no_package_is_declared_in_both_files():
    assert set(declared(read(RUNTIME_IN))) & set(declared(read(DEV_IN))) == set()


def test_only_the_development_file_has_the_constraint_line_and_has_it_once():
    assert CONSTRAINT_LINE not in declared_lines(read(RUNTIME_IN))
    assert declared_lines(read(DEV_IN)).count(CONSTRAINT_LINE) == 1


def test_pydantic_is_declared_by_name():
    # backend/schemas.py imports it, so it is named and not left for fastapi
    # to bring. fastapi 0.143.0 asks for 2.9 or later, which already rules
    # out version 1; this line is what keeps it below 3. The schemas
    # configure themselves in `class Config` blocks, and version 2 warns
    # that version 3 will not read them.
    assert declared(read(RUNTIME_IN))["pydantic"].split(".")[0] == "2"


@pytest.mark.parametrize("name", [RUNTIME_IN, DEV_IN, RUNTIME_LOCK, DEV_LOCK])
def test_passlib_is_in_none_of_the_four_files(name):
    text = read(name)
    names = declared(text) if name.endswith(".in") else versions(text)
    assert "passlib" not in names


# --- The locks ----------------------------------------------------------------

@pytest.mark.parametrize("name", LOCKS)
def test_every_locked_package_has_one_exact_version_and_a_hash(name):
    entries = locked(read(name))
    assert entries
    assert [entry_name for entry_name, version, _, hashes in entries if version is None or not hashes] == []


@pytest.mark.parametrize("name", LOCKS)
def test_a_lock_holds_nothing_but_packages_and_their_hashes(name):
    # No `-r`, `-c`, `-e`, index or other option line: Requirement refuses
    # them, so reading the file at all is the check. What is left over on a
    # line once its hashes are taken off must be the requirement alone.
    for line in read(name).replace("\\\n", " ").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            assert not line.startswith("-"), line
            assert HASH.sub("", line).strip() == re.split(r"\s--hash=", line, maxsplit=1)[0].strip(), line


@pytest.mark.parametrize("name", LOCKS)
def test_a_lock_has_each_package_once(name):
    # One version for every interpreter and platform. The compile command's
    # --fork-strategy fewest is what gives this; without it a newer Python
    # gets a second, newer entry where a release exists that 3.10 cannot run
    # (websockets, when this was written).
    names = [entry_name for entry_name, _, _, _ in locked(read(name))]
    assert sorted(names) == sorted(set(names))


@pytest.mark.parametrize("declared_in, lock", PAIRS)
def test_every_declared_package_is_locked_at_its_declared_version(declared_in, lock):
    wanted = declared(read(declared_in))
    held = versions(read(lock))
    assert {name: held.get(name) for name in wanted} == wanted


def test_a_package_in_both_locks_has_one_version():
    runtime, dev = versions(read(RUNTIME_LOCK)), versions(read(DEV_LOCK))
    shared = set(runtime) & set(dev)
    # Without any shared package this would pass on nothing.
    assert shared
    assert {name: dev[name] for name in shared} == {name: runtime[name] for name in shared}


@pytest.mark.parametrize("declared_in, lock", PAIRS)
def test_a_lock_names_the_command_that_made_it_and_the_in_file_gives_the_same_one(declared_in, lock):
    header = read(lock).splitlines()[:2]
    assert header[0].startswith("# This file was autogenerated by uv")
    command = header[1].lstrip("# ").strip()
    assert command.startswith(f"uv pip compile {declared_in} ") and command.endswith(f" -o {lock}")
    assert "--generate-hashes" in command.split()
    assert f"#   {command}\n" in read(RUNTIME_IN)


# --- What is installed --------------------------------------------------------

def applies(marker):
    return marker is None or marker.evaluate()


@pytest.mark.parametrize("name", LOCKS)
def test_the_suite_is_running_on_the_locked_versions(name):
    # A green run means "tested on the lock" only if this holds. Every locked
    # package meant for this interpreter and platform is installed, at the
    # locked version. To get there:
    #   pip install --require-hashes -r requirements.txt -r requirements-dev.txt
    wanted = {entry: version for entry, version, marker, _ in locked(read(name)) if applies(marker)}
    assert wanted
    installed = {}
    for entry in wanted:
        try:
            installed[entry] = metadata.version(entry)
        except metadata.PackageNotFoundError:
            installed[entry] = None
    assert installed == wanted


# --- The install sites --------------------------------------------------------

# `pip`, the word `install` somewhere after it, and what follows. Whatever
# stands between the two (`pip -q install`) is read as arguments as well, so
# it has to be allowed like the rest.
PIP_INSTALL = re.compile(r"(?:\b|-m)pip[0-9.]*\s+(?:(.*?)\s+)?install(?:\s+(.*))?$")
EXEC_FORM = re.compile(r"\[\s*\".*\"\s*\]")


def as_shell(line):
    """A line holding an exec-form list (`RUN ["pip", "install", "x"]`), with the list written out as words."""
    match = EXEC_FORM.search(line)
    if not match:
        return line
    try:
        words = json.loads(match.group(0))
    except ValueError:
        return line
    return line[:match.start()] + " ".join(str(word) for word in words) + line[match.end():]


def installs(script):
    """The arguments of every `pip install` in a shell script or a Dockerfile, one list each."""
    found = []
    for line in script.replace("\\\n", " ").splitlines():
        line = line.strip()
        if line.startswith("#"):
            continue
        for command in re.split(r"&&|\|\||[;|]", as_shell(line).split(" #", 1)[0]):
            match = PIP_INSTALL.search(command.strip())
            if match:
                found.append(shlex.split(f"{match.group(1) or ''} {match.group(2) or ''}"))
    return found


def refusal(arguments):
    """Why an install's arguments are not allowed, or None when they are.

    Allowed: the pip upgrade, exactly; or --require-hashes with nothing to
    install but `-r <one of the two locks>`.
    """
    if arguments == UPGRADE_PIP:
        return None
    if "--require-hashes" not in arguments:
        return "no --require-hashes"
    files = []
    rest = iter(arguments)
    for argument in rest:
        if argument == "-r":
            files.append(next(rest, None))
        elif argument not in INSTALL_OPTIONS:
            return f"not a lock and not an allowed option: {argument}"
    if not files:
        return "installs no file"
    if not set(files) <= set(LOCKS):
        return f"installs from something other than the locks: {files}"
    return None


def test_the_install_reader_sees_each_form():
    assert installs("python -m pip install --upgrade pip\npip install -r a.txt  # why\n") == [
        ["--upgrade", "pip"], ["-r", "a.txt"],
    ]
    assert installs("apt-get update && pip3 install \\\n  ruff 'pytest<9'; echo done") == [["ruff", "pytest<9"]]
    assert installs("echo pipinstall; pip download ruff; npm install") == []
    assert installs("# pip install ruff\n  # and pip install pytest\n") == []
    assert installs("pip download install-me; pip freeze; pip installer x") == []
    # An option before the word, and a Dockerfile's list form.
    assert installs("pip --no-cache-dir install ruff\npython -m pip -q install -r a.txt\npip install") == [
        ["--no-cache-dir", "ruff"], ["-q", "-r", "a.txt"], [],
    ]
    assert installs("python -mpip install ruff") == [["ruff"]]
    assert installs('RUN ["pip", "install", "ruff"]\nRUN ["sh", "-c", "true && pip install httpx"]') == [
        ["ruff"], ["httpx"],
    ]


@pytest.mark.parametrize("arguments", [
    ["--upgrade", "pip"],
    ["--require-hashes", "-r", "requirements.txt"],
    ["--no-cache-dir", "--require-hashes", "-r", "requirements.txt"],
    ["--require-hashes", "-r", "requirements.txt", "-r", "requirements-dev.txt"],
])
def test_an_allowed_install(arguments):
    assert refusal(arguments) is None


@pytest.mark.parametrize("arguments", [
    ["-r", "requirements.txt"],
    ["--no-cache-dir", "-r", "requirements.txt"],
    ["ruff", "pytest", "httpx"],
    ["--require-hashes", "ruff"],
    ["--require-hashes", "-r", "requirements.txt", "ruff"],
    ["--require-hashes"],
    ["--require-hashes", "-r"],
    ["--require-hashes", "-r", "requirements.in"],
    ["--require-hashes", "-r", "requirements.txt", "--no-deps"],
    ["--require-hashes", "-r", "requirements.txt", "--index-url", "https://example.invalid"],
    ["--upgrade", "pip", "ruff"],
    ["--upgrade", "ruff"],
    ["-q", "--upgrade", "pip"],
    ["-q", "--require-hashes", "-r", "requirements.txt"],
    [],
])
def test_a_refused_install(arguments):
    assert refusal(arguments) is not None


def dockerfile_installs():
    return installs(read("Dockerfile.backend"))


def compose_installs():
    """Every `pip install` in a service's `command:` or `entrypoint:` in docker-compose.yml."""
    found = []
    for service in yaml.safe_load(read("docker-compose.yml"))["services"].values():
        for key in ("command", "entrypoint"):
            value = service.get(key, "")
            found += installs(value if isinstance(value, str) else " ".join(value))
    return found


def workflow_installs():
    """{job: [arguments, ...]} for every job of the CI workflow with a `pip install`."""
    jobs = yaml.safe_load(read(".github/workflows/ci.yml"))["jobs"]
    found = {}
    for name, job in jobs.items():
        in_job = [arguments for step in job["steps"] for arguments in installs(step.get("run", ""))]
        if in_job:
            found[name] = in_job
    return found


def test_the_image_installs_the_runtime_lock_by_hash_and_nothing_else():
    found = dockerfile_installs()
    assert [refusal(arguments) for arguments in found] == [None]
    assert [a for a in found[0] if a in LOCKS] == [RUNTIME_LOCK]


def test_ci_installs_both_locks_by_hash_and_names_no_package_but_pip():
    found = workflow_installs()
    assert list(found) == ["backend"]
    assert [arguments for arguments in found["backend"] if refusal(arguments)] == []
    # Apart from the pip upgrade, one install, and it takes both locks.
    of_the_locks = [arguments for arguments in found["backend"] if arguments != UPGRADE_PIP]
    assert len(of_the_locks) == 1
    assert [a for a in of_the_locks[0] if a in LOCKS] == LOCKS


def test_no_service_of_the_development_stack_installs_a_package_when_it_starts():
    # The backend service runs the image with its own command. A `pip install`
    # put in front of that command would add to what the lock installed.
    services = yaml.safe_load(read("docker-compose.yml"))["services"]
    assert "command" in services["backend"]
    assert compose_installs() == []


# --- The import the ticket named ----------------------------------------------

def imports(source):
    """Every module an import statement reaches: `a.b`, and `a.b.c` for `from a.b import c`."""
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_the_import_reader_sees_each_form():
    assert "sqlalchemy.ext.declarative" in imports("from sqlalchemy.ext.declarative import declarative_base")
    assert "sqlalchemy.ext.declarative" in imports("from sqlalchemy.ext import declarative")
    assert "sqlalchemy.ext.declarative" in imports("def f():\n    import sqlalchemy.ext.declarative as d")
    assert "sqlalchemy.ext.declarative" not in imports("from sqlalchemy.orm import declarative_base, sessionmaker")


def test_nothing_imports_the_declarative_base_from_its_deprecated_home():
    # sqlalchemy.orm is where 2.x keeps declarative_base. The old path still
    # answers on 2.0, with a deprecation warning.
    files = sorted(BACKEND.rglob("*.py")) + sorted((ROOT / "alembic").rglob("*.py"))
    assert BACKEND / "database.py" in files
    offenders = [
        str(path.relative_to(ROOT))
        for path in files
        if any(name.startswith("sqlalchemy.ext.declarative") for name in imports(path.read_text(encoding="utf-8")))
    ]
    assert offenders == []
