"""INF-H10 -- the CI step that runs these tests fails when pytest does.

The `Run tests` step of .github/workflows/ci.yml used to wrap pytest in a
script that turned exit code 5 into success. pytest gives 5 when it collected
no tests, and the script dated from when there were none. With it in place, a
run that collected nothing passed. The step is now the command alone.

Nothing here runs the workflow. The tests read ci.yml and check that the step
is still the command alone, and that neither the step nor its job has gained a
key that decides whether a failure counts.

What is NOT covered here:
  - a run that collects nothing. These are pytest tests, so such a run does
    not collect them either. The step itself fails that run, on exit code 5;
    what these tests can catch is the step being changed while they still run;
  - a run that collects fewer tests than it should. Nothing counts them;
  - tests that skip. The step passes however many do;
  - pytest's own configuration. A configuration file, a conftest.py or a
    plugin can deselect tests without the workflow changing. Run from the
    top of the repository, as the step runs it, pytest reports no
    configuration file today: backend/pyproject.toml has a pytest section
    and is not read;
  - the rest of the workflow, beyond its `defaults`, its `env`, and a
    search of every step for the word `pytest`. What the checkout step
    fetches, a step that deletes test files, a script kept in another file,
    a variable's name that a script puts together, a variable that changes
    what the shell or Python loads, and the events the workflow runs on are
    not looked at;
  - the `frontend` job, which runs its tests with another tool.

PyYAML is imported here. requirements-dev.in names it, so the tests do not
depend on `uvicorn[standard]` bringing it.
"""
import os

import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CI_JOB = "backend"
TEST_STEP = "Run tests"
COMMAND = "python -m pytest"


def read(*parts):
    with open(os.path.join(PROJECT_ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


# --- Reading the CI job ------------------------------------------------------

def workflow():
    return yaml.safe_load(read(".github", "workflows", "ci.yml"))


def ci_job():
    jobs = workflow()["jobs"]
    assert CI_JOB in jobs, f"ci.yml has no `{CI_JOB}` job"
    return jobs[CI_JOB]


def step_named(name):
    steps = [step for step in ci_job()["steps"] if step.get("name") == name]
    assert len(steps) == 1, f"expected one step named `{name}`, found {len(steps)}"
    return steps[0]


def script_lines(step):
    return [line.strip() for line in step["run"].splitlines() if line.strip()]


def steps_mentioning_pytest():
    """(job, step name) for every step of the workflow with the word pytest in it.

    In any key or value of the step and in either case, so that a step writing
    PYTEST_ADDOPTS to $GITHUB_ENV, for the steps after it, is one of them
    whether the name is in its script or in its `env`. A name put together
    by the script, or written by a script in another file, is not found.
    """
    return [
        (name, step.get("name"))
        for name, job in workflow()["jobs"].items()
        for step in job["steps"]
        if "pytest" in yaml.safe_dump(step).lower()
    ]


# --- The step ----------------------------------------------------------------

def test_one_step_of_the_workflow_names_pytest():
    # Another step that names it may run the tests a second time, where the
    # tests below do not look, or set a variable pytest reads.
    assert steps_mentioning_pytest() == [(CI_JOB, TEST_STEP)]


def test_the_step_is_the_command_and_nothing_else():
    # Line for line, less the space around each line and the blank ones.
    # `set +e`, `|| true`, `; exit 0`, a pipe, and an option or a path that
    # narrows what is collected would each change the line or add one.
    assert script_lines(step_named(TEST_STEP)) == [COMMAND]


def test_the_step_cannot_be_skipped_or_forgiven():
    # The whole key set: no `if`, no `continue-on-error`, and no `shell`,
    # `env` or `working-directory` to change what the command means.
    assert set(step_named(TEST_STEP)) == {"name", "run"}


# --- The job -----------------------------------------------------------------

def test_the_job_cannot_be_skipped_or_forgiven():
    # The whole key set, as above. An `if` here skips the job, and a
    # `continue-on-error` lets the run pass when the job fails.
    assert set(ci_job()) == {"runs-on", "services", "env", "steps"}


def test_the_workflow_sets_no_default_for_its_steps():
    # `defaults` at the top of the workflow gives a `shell` or a
    # `working-directory` to each `run` step that has none of its own from
    # its job or itself, which this step's key set would not show. The job's
    # key set already refuses one on the job.
    assert "defaults" not in workflow()


def test_neither_env_block_above_the_step_names_a_pytest_variable():
    # pytest reads PYTEST_ADDOPTS as more of its command line, so `--co`
    # there stops the tests from running with the step unchanged. A name
    # that starts the same way is refused in the workflow's `env` and in the
    # job's.
    names = list(workflow().get("env", {})) + list(ci_job()["env"])
    assert [name for name in names if name.upper().startswith("PYTEST")] == []
