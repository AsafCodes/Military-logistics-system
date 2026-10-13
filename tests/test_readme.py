"""DOC-H4 -- the two readmes name things the repository has.

The repository had no README.md and no LICENSE at its top: both were deleted
by a revert and not put back. The one readme left, frontend/README.md, was
the template Vite writes. The readme that was deleted gave a frontend port
the stack does not publish, a `docker-compose` command, and a server command
that cannot import the application (`uvicorn main:app` from inside
`backend/`). Both readmes are written again and the license is put
back. These tests read the three files and compare what the readmes name
with docker-compose.yml, .env.example, frontend/package.json, the workflow
and the files under backend/.

Nothing here runs a command or starts the stack, and nothing is imported
from the backend.

What is read:
  - a backticked text: what stands between a pair of backticks on one line,
    outside a fenced block. A line with an odd number of backticks loses
    its last one. Backticks are counted one by one, so text marked with
    two on each side is misread;
  - a section: the lines outside fenced blocks from its heading to the next
    line that starts `## `;
  - a quick-start command: the first backticked text of a line of the root
    readme's `## Quick start` section that starts `- `;
  - an exec command: every backticked text of that section that starts
    `docker compose exec `, a list item's or not;
  - a link: `[words](target)` outside a fenced block, with nothing in the
    parentheses but the target. A target with `://` in it, and one that
    starts `#` or `mailto:`, is left alone. An image is read as a link,
    and so is a link written inside backticks;
  - an address: each `localhost:<digits>` in a readme, anywhere. Both
    readmes' addresses are compared with the ports docker-compose.yml
    publishes, the frontend's too;
  - each readme must have a link and an address, the root readme a Python
    version and the frontend's a Node version. One that loses its last
    fails;
  - a variable to fill in: a backticked text of the quick start, outside a
    fenced block, that is capitals, digits and underscores and nothing
    else. Any such text there is taken for one, whatever the words around
    it say;
  - a version: each `Python <digits>.<digits>` in the root readme, and each
    `Node <digits>` or `Node.js <digits>` in the frontend's.

What is NOT covered here:
  - that a command works. The quick start was run once by hand on a new
    volume (2026-10-13); nothing runs it again;
  - what a readme says in words: what the seed makes and prints, how one
    logs in, the technology list, the tree of folders, what a document is
    said to hold;
  - a command of the quick start that is not a list item's first backticked
    text, such as one in a fenced block. It is not read;
  - a quick-start command of another kind than `cp`, `docker compose up`
    and `docker compose exec`. It is refused, whatever it is;
  - the options of `docker compose up`;
  - an exec command in another form than `-e NAME=value` options and
    options that take no value, then the service, then
    `python -m backend.<module>` and what the module is given. Another is
    refused, even one that works: `--env`, `--user 0` and `-w dir` are
    misread, and so is a command that is not a module of backend/. The
    service, each `-e` name and the module are compared. The value is
    not, and neither is what the module is given. A name counts as read
    by the module when the module's file has `os.getenv("NAME"`; one read
    another way fails;
  - that the frontend's addresses are the ones its own files give. The
    port is in frontend/vite.config.ts and the API's default address in
    frontend/src/lib/axios.ts, and neither is read;
  - whether an address answers, and what follows the port: `/docs` and
    `/health` are not compared with the application's routes;
  - a published port written another way than `"host:container"`, a short
    string of two numbers. One bound to an address or given as a mapping
    reads as not published, and its address in a readme fails;
  - whether a variable the readme says to fill in is enough. The names are
    the ones docker-compose.yml refuses to start without (`${NAME:?...}`
    or `${NAME?...}` in a value of the file, not in a comment); a variable
    the stack needs and does not ask for like that is not seen;
  - the template under another title and another first sentence than the
    ones Vite writes;
  - a link with a title, in angle brackets, or by reference. It is not
    read;
  - the license's words below its first line, whether it is the license
    its holder means, and what GitHub shows for the repository;
  - a link to a folder's own page or to a line (`#L10`): a target must be a
    file or a folder that is there, and an anchor a heading of it;
  - a link's anchor in another form than GitHub's for a plain heading:
    lower case, spaces to hyphens, other punctuation dropped. A heading
    that is repeated, and one with a link in it, is not worked out. A
    letter outside a-z is dropped where GitHub keeps it, so a heading in
    Hebrew cannot be linked to. A line `#word` is taken for a heading;
  - a readme anywhere else than the top of the repository and frontend/.
"""
import json
import os
import re

import pytest
import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ROOT_README = ("README.md",)
FRONTEND_README = ("frontend", "README.md")
READMES = (ROOT_README, FRONTEND_README)
LICENSE = ("LICENSE",)

QUICK_START = "## Quick start"
LICENSE_SECTION = "## License"
ITEM_PREFIX = "- "
FENCE = "```"
NPM_RUN = "npm run "
COMPOSE = "docker compose "

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
ADDRESS = re.compile(r"localhost:(\d+)")
VARIABLE = re.compile(r"[A-Z][A-Z0-9_]*")
REQUIRED = re.compile(r"\$\{([A-Z][A-Z0-9_]*):?\?")
OLD_COMPOSE = re.compile(r"docker-compose(?!\.yml)")
PYTHON_VERSION = re.compile(r"Python (\d+\.\d+)")
NODE_VERSION = re.compile(r"Node(?:\.js)? (\d+)")
GETENV = re.compile(r"""os\.getenv\(\s*["']([A-Z][A-Z0-9_]*)["']""")

BACKEND_JOB = "backend"
FRONTEND_JOB = "frontend"
PYTHON_STEP = "Set up Python"
NODE_STEP = "Set up Node"

# The first line of the template `npm create vite` writes, and the start of
# its first sentence.
TEMPLATE_MARKS = ("# React + TypeScript + Vite", "This template provides")


def path_of(*parts):
    return os.path.join(PROJECT_ROOT, *parts)


def read(*parts):
    with open(path_of(*parts), encoding="utf-8") as handle:
        return handle.read()


def is_there(path):
    """Whether `path` exists as it is written, letter case included.

    Windows finds `license` where the file is `LICENSE`; GitHub, which shows
    the readme, does not.
    """
    folder, parts = PROJECT_ROOT, os.path.relpath(path, PROJECT_ROOT).split(os.sep)
    for part in parts:
        if part == os.pardir or part not in os.listdir(folder):
            return False
        folder = os.path.join(folder, part)
    return True


# --- Reading a readme --------------------------------------------------------

def prose(text):
    """The lines outside fenced blocks."""
    kept, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith(FENCE):
            fenced = not fenced
        elif not fenced:
            kept.append(line)
    return kept


def section(text, heading):
    """The lines under `heading`, to the next line that starts `## `.

    Fenced blocks are left out first, so a line of a block is neither a
    heading nor the end of a section.
    """
    lines = prose(text)
    starts = [i for i, line in enumerate(lines) if line.rstrip() == heading]
    assert len(starts) == 1, f"{len(starts)} lines `{heading}`"
    body = []
    for line in lines[starts[0] + 1:]:
        if line.startswith("## "):
            break
        body.append(line)
    return body


def backticked(lines):
    return [piece for line in lines for piece in line.split("`")[1:-1:2]]


def commands(lines):
    """The first backticked text of each list item that has one."""
    found = []
    for line in lines:
        pieces = line.split("`")
        if line.startswith(ITEM_PREFIX) and len(pieces) >= 3:
            found.append(pieces[1])
    return found


def links(lines):
    found = LINK.findall("\n".join(lines))
    return [t for t in found if "://" not in t and not t.startswith(("#", "mailto:"))]


def anchor_of(heading_line):
    """GitHub's anchor for a plain heading."""
    words = heading_line.lstrip("#").strip().lower()
    return re.sub(r"[^a-z0-9 _-]", "", words).replace(" ", "-")


def anchors(text):
    return [anchor_of(line) for line in prose(text) if line.startswith("#")]


def exec_parts(command):
    """The `-e` names, the service and the words after it, of an exec command."""
    words = command.split()
    assert words[:3] == ["docker", "compose", "exec"], command
    names, rest = [], words[3:]
    while rest and rest[0].startswith("-"):
        if rest[0] == "-e":
            names.append(rest[1].split("=")[0])
            rest = rest[2:]
        else:
            rest = rest[1:]
    return names, rest[0], rest[1:]


def quick_start():
    return section(read(*ROOT_README), QUICK_START)


def quick_start_commands():
    return commands(quick_start())


def quick_start_exec_commands():
    return [text for text in backticked(quick_start()) if text.startswith(COMPOSE + "exec ")]


# --- Reading what the readmes are compared with ------------------------------

def compose():
    return yaml.safe_load(read("docker-compose.yml"))


def published_ports(document):
    """The host ports of every `"host:container"` entry of the services."""
    found = set()
    for service in document["services"].values():
        for entry in service.get("ports", []):
            parts = str(entry).split(":")
            if len(parts) == 2:
                found.add(parts[0])
    return found


def strings(value):
    """Every string of a loaded YAML value, keys left out."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return [found for item in value for found in strings(item)]
    return []


def required_variables(document):
    """The names of every `${NAME:?...}` and `${NAME?...}` in the file's values."""
    return set(REQUIRED.findall("\n".join(strings(document))))


def scripts():
    return json.loads(read("frontend", "package.json"))["scripts"]


def setup_version(job, step_name, key):
    steps = [
        step for step in yaml.safe_load(read(".github", "workflows", "ci.yml"))["jobs"][job]["steps"]
        if step.get("name") == step_name
    ]
    assert len(steps) == 1, f"expected one `{step_name}` step in `{job}`, found {len(steps)}"
    return str(steps[0]["with"][key])


# --- The readers, on lines written here --------------------------------------

def test_prose_leaves_out_fenced_blocks():
    text = "one\n```bash\n- `npm run inside`\n```\ntwo\n  ```\nthree\n  ```\nfour"
    assert prose(text) == ["one", "two", "four"]


def test_a_section_does_not_end_or_start_inside_a_fenced_block():
    text = "## A\none\n```\n## B\n- `inside`\n```\ntwo\n## B\nthree\n"
    assert section(text, "## A") == ["one", "two"]
    assert section(text, "## B") == ["three"]
    for heading in ("## C", "## A "):
        with pytest.raises(AssertionError):
            section(text, heading)
    with pytest.raises(AssertionError):
        section("## A\n## A\n", "## A")


def test_backticked_text_is_read_in_pairs():
    assert backticked(["a `b` c `d`", "no ticks", "odd `e` and ` one"]) == ["b", "d", "e"]


def test_a_command_is_the_first_backticked_text_of_a_list_item():
    lines = [
        "Needs `docker compose`.",
        "- Copy: `cp a b`. Then fill `NAME`.",
        "- No command here.",
        "  - Indented: `npm run nested`",
        "- Start: `docker compose up`",
    ]
    assert commands(lines) == ["cp a b", "docker compose up"]


def test_a_link_is_read_unless_it_leaves_the_repository_or_stays_on_the_page():
    lines = [
        "[a](A.md) and [b](dir/B.md#part), [site](https://example.test/x.md),",
        "[up](#top), [mail](mailto:a@example.test), [c](../C.md)",
    ]
    assert links(lines) == ["A.md", "dir/B.md#part", "../C.md"]


def test_an_anchor_is_the_heading_in_lower_case_with_hyphens():
    assert anchor_of("## Testing") == "testing"
    assert anchor_of("### Quick start (Docker)") == "quick-start-docker"
    assert anchors("# One\n```\n# not a heading\n```\n## Two words\ntext") == ["one", "two-words"]


def test_an_exec_command_gives_its_names_its_service_and_the_rest():
    assert exec_parts("docker compose exec -T -e A=1 -e B=2 web python -m pkg.mod x") == (
        ["A", "B"], "web", ["python", "-m", "pkg.mod", "x"],
    )
    assert exec_parts("docker compose exec web sh") == ([], "web", ["sh"])
    with pytest.raises(AssertionError):
        exec_parts("docker compose run web sh")


def test_a_path_is_there_only_as_it_is_written():
    assert is_there(path_of("tests", "test_readme.py"))
    assert is_there(path_of("tests"))
    assert not is_there(path_of("tests", "TEST_README.py"))
    assert not is_there(path_of("Tests", "test_readme.py"))
    assert not is_there(path_of("tests", "no_such_file.py"))
    # Not a way out of the repository.
    assert not is_there(os.path.dirname(PROJECT_ROOT))


def test_a_required_variable_is_one_a_value_of_the_file_refuses_to_be_without():
    document = yaml.safe_load(
        "# ${COMMENTED:?not a value}\n"
        "services:\n"
        "  a:\n"
        "    environment:\n"
        "      - ONE=${ONE:?set ONE}\n"
        "      - URL=db://${TWO?set TWO}:${THREE:-three}@host/${FOUR}\n"
        "    image: ${FIVE:?}\n"
        "  ${SIX:?a key}: {}\n"
    )
    assert required_variables(document) == {"ONE", "TWO", "FIVE"}


def test_a_published_port_is_the_host_side_of_a_pair():
    document = {"services": {
        "a": {"ports": ["8000:8000", "127.0.0.1:9000:9000", {"published": 7000, "target": 7000}]},
        "b": {"ports": ["13000:3000"]},
        "c": {},
    }}
    assert published_ports(document) == {"8000", "13000"}


# --- The three files ---------------------------------------------------------

@pytest.mark.parametrize("parts", [ROOT_README, FRONTEND_README, LICENSE])
def test_the_file_is_there_and_has_words(parts):
    assert is_there(path_of(*parts)) and os.path.isfile(path_of(*parts))
    assert read(*parts).strip()


def test_the_frontend_readme_is_not_the_template():
    text = read(*FRONTEND_README)
    assert [mark for mark in TEMPLATE_MARKS if mark in text] == []


def test_the_readme_gives_the_copyright_line_of_the_license():
    first = read(*LICENSE).splitlines()[0].strip()
    assert "All Rights Reserved" in first
    given = [line.strip() for line in section(read(*ROOT_README), LICENSE_SECTION)]
    assert first in given


# --- Links -------------------------------------------------------------------

@pytest.mark.parametrize("parts", READMES)
def test_every_link_names_a_file_and_a_heading_of_it(parts):
    folder = os.path.dirname(path_of(*parts))
    targets = links(prose(read(*parts)))
    assert targets
    missing = []
    for target in targets:
        file_part, _, anchor = target.partition("#")
        linked = os.path.normpath(os.path.join(folder, file_part))
        if not is_there(linked):
            missing.append(target)
        elif anchor:
            with open(linked, encoding="utf-8") as handle:
                if anchor not in anchors(handle.read()):
                    missing.append(target)
    assert missing == []


# --- Commands and addresses, in both readmes ---------------------------------

@pytest.mark.parametrize("parts", READMES)
def test_every_npm_command_is_npm_ci_or_a_script(parts):
    given = [text for text in backticked(prose(read(*parts))) if text.startswith("npm ")]
    known = scripts()
    assert [
        text for text in given
        if text != "npm ci" and not (text.startswith(NPM_RUN) and text[len(NPM_RUN):] in known)
    ] == []


def test_the_frontend_readme_gives_npm_commands():
    given = [text for text in backticked(prose(read(*FRONTEND_README))) if text.startswith(NPM_RUN)]
    # Five today: the server, the linter, the typecheck, the tests, the build.
    assert len(given) >= 5 and len(set(given)) == len(given)


@pytest.mark.parametrize("parts", READMES)
def test_every_local_address_has_a_port_the_stack_publishes(parts):
    given = ADDRESS.findall(read(*parts))
    assert given
    assert [port for port in given if port not in published_ports(compose())] == []


@pytest.mark.parametrize("parts", READMES)
def test_no_readme_has_the_old_compose_command(parts):
    assert OLD_COMPOSE.findall(read(*parts)) == []


def test_the_python_version_is_the_one_the_ci_sets_up():
    given = PYTHON_VERSION.findall(read(*ROOT_README))
    assert given and set(given) == {setup_version(BACKEND_JOB, PYTHON_STEP, "python-version")}


def test_the_node_version_is_the_one_the_ci_sets_up():
    given = NODE_VERSION.findall(read(*FRONTEND_README))
    assert given and set(given) == {setup_version(FRONTEND_JOB, NODE_STEP, "node-version")}


# --- The quick start ---------------------------------------------------------

def test_every_quick_start_command_is_of_a_kind_that_is_compared():
    given = quick_start_commands()
    assert [
        command for command in given
        if not command.startswith(("cp ", COMPOSE + "up", COMPOSE + "exec "))
    ] == []
    # One of each today.
    assert len(given) >= 3


def test_the_file_the_quick_start_copies_is_there():
    given = [command.split() for command in quick_start_commands() if command.startswith("cp ")]
    assert given == [["cp", ".env.example", ".env"]]
    assert is_there(path_of(".env.example"))


def test_the_variables_to_fill_in_are_the_ones_the_stack_refuses_to_start_without():
    given = {text for text in backticked(quick_start()) if VARIABLE.fullmatch(text)}
    assert given == required_variables(compose())
    example = read(".env.example").splitlines()
    # A line that sets the name, not one that is a comment about it.
    assert [
        name for name in sorted(given)
        if not any(line.startswith(f"{name}=") for line in example)
    ] == []


def test_the_exec_command_names_a_service_a_module_and_a_variable_it_reads():
    given = quick_start_exec_commands()
    assert given
    for command in given:
        names, service, rest = exec_parts(command)
        assert service in compose()["services"], command
        assert rest[:2] == ["python", "-m"] and len(rest) >= 3, command
        package, _, module = rest[2].partition(".")
        assert package == "backend" and module and "." not in module, command
        assert is_there(path_of("backend", module + ".py")), command
        assert names, command
        read_by_module = GETENV.findall(read("backend", module + ".py"))
        assert [name for name in names if name not in read_by_module] == [], command
