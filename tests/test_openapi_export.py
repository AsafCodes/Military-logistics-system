"""API-H1 -- the OpenAPI specification is generated, never tracked.

frontend/openapi.json was a tracked copy of the specification that nobody
regenerated: it declared version "4.0 - Secured" against the app's 0.5.0, and
listed ten paths -- two of them since removed -- where the app serves
twenty-nine. It was the input the generated frontend client was built from,
until API-H2 deleted that client and its generator.

The fix is to stop tracking it and generate it from the application on demand
(backend/export_openapi.py), in CI on every green run. With no tracked copy there is
nothing left to drift from, so these tests pin the two things that keep it that
way: the exporter emits exactly what the running app serves, and no copy of the
specification is checked in again.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

from backend.main import app

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC_NAME = "openapi.json"


def _run(args, cwd=PROJECT_ROOT, env_overrides=None):
    """Run a fresh interpreter with `args`; stdout and stderr come back as bytes.

    Bytes, not text, because line endings are part of what is asserted and
    text mode would translate them away before the test could see them.

    An override whose value is None removes the variable from the child.
    """
    env = {
        **os.environ,
        "SECRET_KEY": "test_secret_key",
        "PYTHONPATH": os.pathsep.join(
            p for p in (PROJECT_ROOT, os.environ.get("PYTHONPATH")) if p
        ),
        "PYTHONIOENCODING": "utf-8",
    }
    for name, value in (env_overrides or {}).items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return subprocess.run(
        [sys.executable, *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        timeout=60,
        check=False,
    )


def _export(*argv):
    return _run(["-m", "backend.export_openapi", *argv])


def _live_spec():
    # Round-tripped so the comparison is between two things JSON can hold.
    return json.loads(json.dumps(app.openapi()))


def test_the_exported_file_is_the_live_application_spec(tmp_path):
    target = tmp_path / SPEC_NAME

    result = _export(str(target))

    assert result.returncode == 0, result.stderr.decode()
    spec = json.loads(target.read_bytes())
    assert spec == _live_spec()
    # The field the stale copy got wrong, asserted on its own so a failure
    # names it rather than burying it in a whole-document diff.
    assert spec["info"]["version"] == app.version


def test_the_exported_file_has_lf_endings_and_one_final_newline(tmp_path):
    target = tmp_path / SPEC_NAME

    assert _export(str(target)).returncode == 0
    raw = target.read_bytes()

    assert b"\r" not in raw
    assert raw.endswith(b"}\n")


def test_stdout_carries_the_same_bytes_as_the_file(tmp_path):
    target = tmp_path / SPEC_NAME
    assert _export(str(target)).returncode == 0

    result = _export()

    assert result.returncode == 0, result.stderr.decode()
    # Byte equality, not JSON equality: anything else printed on import
    # (a banner, a warning sent to stdout) or a CRLF translation on Windows
    # would corrupt `> openapi.json` while still parsing as the same document.
    assert result.stdout == target.read_bytes()


def test_an_unwritable_path_fails_with_a_message_not_a_traceback(tmp_path):
    target = tmp_path / "no-such-directory" / SPEC_NAME

    result = _export(str(target))

    stderr = result.stderr.decode()
    assert result.returncode == 1
    assert "cannot write" in stderr
    assert str(target) in stderr
    assert "Traceback" not in stderr
    assert not target.exists()


def test_more_than_one_argument_is_refused_with_usage(tmp_path):
    first, second = tmp_path / "a.json", tmp_path / "b.json"

    result = _export(str(first), str(second))

    assert result.returncode == 2
    assert b"usage:" in result.stderr
    assert not first.exists() and not second.exists()


def test_a_missing_database_url_is_refused_not_defaulted(tmp_path):
    """The exporter must not paper over DATA-H11 to make itself convenient.

    Run as `-c` from tmp_path so dotenv searches tmp_path rather than the repo,
    whose .env may well name a database (see test_app_startup.py's DATA-H11
    tests for why the directory decides that). The child asserts it found no
    .env, or a passing result here would prove nothing.
    """
    code = (
        "import sys, runpy, dotenv\n"
        "assert not dotenv.load_dotenv(), 'a .env was found; this test is vacuous'\n"
        f"sys.argv = ['export_openapi', {SPEC_NAME!r}]\n"
        "runpy.run_module('backend.export_openapi', run_name='__main__')\n"
    )

    result = _run(["-c", code], cwd=tmp_path, env_overrides={"DATABASE_URL": None})

    assert result.returncode != 0
    assert b".env.example" in result.stderr
    assert not (tmp_path / SPEC_NAME).exists()


def _git(*args):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    result = subprocess.run(
        ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode == 128:
        pytest.skip(f"not a git checkout: {result.stderr.strip()}")
    return result


def test_no_openapi_specification_is_tracked_anywhere():
    result = _git("ls-files", "-z")
    # Without this, any failure but the 128 _git skips on would list nothing,
    # and nothing listed reads as nothing tracked.
    assert result.returncode == 0, result.stderr
    tracked = result.stdout.split("\0")

    specs = [path for path in tracked if path.rsplit("/", 1)[-1] == SPEC_NAME]

    assert specs == [], (
        f"{specs} tracked. Generate the specification with "
        "`python -m backend.export_openapi <path>` instead of checking it in -- "
        "a tracked copy is how API-H1 happened."
    )


def test_the_frontend_spec_path_is_ignored():
    # The path CI exports to and the one a developer is pointed at. Ignored so
    # that `git add -A` cannot re-track it by accident.
    result = _git("check-ignore", "-q", f"frontend/{SPEC_NAME}")

    assert result.returncode == 0
