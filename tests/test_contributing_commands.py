"""DOC-H3 -- the commands CONTRIBUTING.md gives are the ones the CI runs.

The guide's `## Testing` section said to run `pytest`. From the top of the
repository that stops before a test runs: tests/conftest.py imports
`backend`, and a bare `pytest` does not put the directory it is run from on
Python's path. `python -m pytest` does, and the CI step runs it from the top
of the repository. The section named nothing to install and no Python
version either. Nothing compared the guide with the workflow. These tests
read the section and compare its commands with .github/workflows/ci.yml and
frontend/package.json.

Nothing here runs a command or the workflow, and nothing is imported from
the backend.

What is read:
  - the section: from the line `## Testing` to the next line that starts
    `## `, or to the end of the file;
  - a command: the first backticked text of a line of that section that
    starts `- `. What else such a line has in backticks is prose about the
    command, and so is every backtick on a line that is not a list item;
  - the Python version: each `Python <digits>.<digits>` in the section.

What is NOT covered here:
  - that a command passes, or runs at all, on a contributor's machine;
  - the guide outside that section, but for one thing step 5 of its workflow
    says: that a CI run exists on a pull request to `main` to wait for;
  - what the section says in words: the directory a command is run in, the
    `npm ci` before the frontend's commands, the sentence about a bare
    `pytest`, and the words around the Python version. `Python 3.10 or
    newer` reads as 3.10, and a version written without the word `Python`
    is not seen;
  - a command of the section that is not a list item's first backticked
    text. It is not read. That includes an item that starts `* ` or `1. `,
    an indented item, and a fenced code block;
  - a list item with a command of another kind. It is refused, even one the
    CI runs, such as `npm ci` or `alembic upgrade head`: only the pytest
    command, one `pip ` line and `npm run ` scripts are compared with
    anything;
  - whether the CI step that has a command is skipped or forgiven, by an
    `if`, a `continue-on-error` or a `shell`, or has the line where its
    script does not reach it. tests/test_ci_test_gate.py holds that for the
    backend's test step, and tests under frontend/src hold it for the
    frontend's audit, lint, typecheck and build steps. This file holds it
    for no step, and nothing holds it for the frontend's `Run tests` step;
  - a command the CI runs and the section does not give, such as
    `npm run build`;
  - what a script of package.json does. `npm run test` is compared by its
    name;
  - a pull request the run leaves out. `paths`, `paths-ignore`, `types`
    and `branches-ignore` under `pull_request` are not read. A branch
    pattern is not understood: `main` must be named, or the event must have
    no `branches` at all, so `**` fails, and a later `!main` that takes the
    branch out again is not seen;
  - whether GitHub makes a merge wait for the run. That is a setting of the
    repository and no file here has it.
"""
import json
import os
import re

import pytest
import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GUIDE = "CONTRIBUTING.md"
SECTION = "## Testing"
ITEM_PREFIX = "- "
PYTHON_VERSION = re.compile(r"Python (\d+\.\d+)")

BACKEND_JOB = "backend"
FRONTEND_JOB = "frontend"
TEST_STEP = "Run tests"
INSTALL_STEP = "Install dependencies"
PYTHON_STEP = "Set up Python"
NPM_RUN = "npm run "


def read(*parts):
    with open(os.path.join(PROJECT_ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


# --- Reading the guide -------------------------------------------------------

def section(text):
    """The lines of `## Testing`, without its heading."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.rstrip() == SECTION]
    assert len(starts) == 1, f"{GUIDE} has {len(starts)} lines `{SECTION}`"
    body = []
    for line in lines[starts[0] + 1:]:
        if line.startswith("## "):
            break
        body.append(line)
    return body


def commands(lines):
    """The first backticked text of each list item that has one."""
    found = []
    for line in lines:
        pieces = line.split("`")
        if line.startswith(ITEM_PREFIX) and len(pieces) >= 3:
            found.append(pieces[1])
    return found


def is_pytest(command):
    return "pytest" in command.split()


def guide_commands():
    return commands(section(read(GUIDE)))


def guide_python_versions():
    return PYTHON_VERSION.findall("\n".join(section(read(GUIDE))))


# --- Reading the workflow ----------------------------------------------------

def workflow():
    return yaml.safe_load(read(".github", "workflows", "ci.yml"))


def step_named(job, name):
    steps = [step for step in workflow()["jobs"][job]["steps"] if step.get("name") == name]
    assert len(steps) == 1, f"expected one `{name}` step in `{job}`, found {len(steps)}"
    return steps[0]


def script_lines(step):
    return [line.strip() for line in step["run"].splitlines() if line.strip()]


def job_script_lines(job):
    return [
        line
        for step in workflow()["jobs"][job]["steps"] if "run" in step
        for line in script_lines(step)
    ]


def triggers():
    # YAML 1.1 reads the bare key `on` as the boolean true, and PyYAML
    # follows it.
    document = workflow()
    return document.get("on", document.get(True))


def runs_on_pull_request_to(branch, events):
    """Whether `events`, a workflow's `on`, has `pull_request` for `branch`.

    GitHub takes one event's name, a list of names, or a mapping from a name
    to its filters. Without a `branches` filter the event is for every
    branch; with one, read as one name or as a list of names, the branch
    must be named in it.
    """
    if isinstance(events, str):
        return events == "pull_request"
    if isinstance(events, list):
        return "pull_request" in events
    if not isinstance(events, dict) or "pull_request" not in events:
        return False
    filters = events["pull_request"] or {}
    if "branches" not in filters:
        return True
    branches = filters["branches"]
    return branch in (branches if isinstance(branches, list) else [branches])


# --- The readers, on lines written here --------------------------------------

def test_a_command_is_the_first_backticked_text_of_a_list_item():
    lines = [
        "Backend, in `backend/`, after `pip install x`:",
        "",
        "- Run the tests: `python -m pytest`. A bare `pytest` stops.",
        "- No command on this line.",
        "- One backtick ` only.",
        "  - Indented: `npm run nested`",
        "- Audit: `npm run audit:prod`",
    ]
    assert commands(lines) == ["python -m pytest", "npm run audit:prod"]


def test_a_pytest_command_has_pytest_as_a_word_of_its_own():
    for command in ("pytest", "python -m pytest", "pytest -x tests"):
        assert is_pytest(command), command
    for command in ("pip install pytest-cov", "python tests/test_pytest.py", "npm run test"):
        assert not is_pytest(command), command


def test_the_section_ends_at_the_next_line_that_starts_with_two_hashes():
    text = "## Code\n- `a`\n## Testing\none\n### Sub\ntwo\n## After\n- `b`\n"
    assert section(text) == ["one", "### Sub", "two"]
    assert section("## Testing  \nlast\n") == ["last"]


def test_the_section_is_refused_without_its_heading_or_with_two():
    for text in ("## Code\n- `a`\n", "## Testing\n## Testing\n", "## Testing notes\n"):
        with pytest.raises(AssertionError):
            section(text)


def test_a_pull_request_event_is_read_in_each_form_github_takes():
    for events in (
        "pull_request",
        ["push", "pull_request"],
        {"pull_request": None},
        {"pull_request": {"types": ["opened"]}},
        {"pull_request": {"branches": ["main"]}},
        {"pull_request": {"branches": ["dev", "main"]}},
        {"pull_request": {"branches": "main"}},
    ):
        assert runs_on_pull_request_to("main", events), events
    for events in (
        None,
        "push",
        ["push", "workflow_dispatch"],
        {"push": {"branches": ["main"]}, "workflow_dispatch": None},
        {"pull_request": {"branches": ["dev"]}},
        {"pull_request": {"branches": []}},
        # Not a substring of the one branch named.
        {"pull_request": {"branches": "maintenance"}},
        {"pull_request_target": {"branches": ["main"]}},
    ):
        assert not runs_on_pull_request_to("main", events), events


# --- The backend's commands --------------------------------------------------

def test_the_one_pytest_command_is_the_script_of_the_ci_step():
    given = [command for command in guide_commands() if is_pytest(command)]
    # Two in the section, or none, is not the step's one line either.
    assert given == script_lines(step_named(BACKEND_JOB, TEST_STEP))


def test_the_install_command_is_a_line_of_the_ci_step():
    given = [command for command in guide_commands() if command.startswith("pip ")]
    assert len(given) == 1
    assert given[0] in script_lines(step_named(BACKEND_JOB, INSTALL_STEP))


def test_the_python_version_is_the_one_the_ci_sets_up():
    given = guide_python_versions()
    assert given == [str(step_named(BACKEND_JOB, PYTHON_STEP)["with"]["python-version"])]


# --- The frontend's commands -------------------------------------------------

def test_every_npm_command_is_a_script_and_a_line_of_the_ci_job():
    given = [command for command in guide_commands() if command.startswith("npm ")]
    scripts = json.loads(read("frontend", "package.json"))["scripts"]
    ran = job_script_lines(FRONTEND_JOB)
    # Four today: the linter, the typecheck, the tests and the audit.
    assert len(given) >= 4 and len(set(given)) == len(given)
    assert [command for command in given if not command.startswith(NPM_RUN)] == []
    assert [command for command in given if command[len(NPM_RUN):] not in scripts] == []
    assert [command for command in given if command not in ran] == []


def test_every_command_of_the_section_is_one_of_the_kinds_above():
    # A command the tests above do not take up would be compared with nothing.
    assert [
        command for command in guide_commands()
        if not (is_pytest(command) or command.startswith(("pip ", "npm ")))
    ] == []


# --- The run step 5 of the guide waits for -----------------------------------

def test_the_workflow_runs_on_a_pull_request_to_main():
    assert runs_on_pull_request_to("main", triggers())
