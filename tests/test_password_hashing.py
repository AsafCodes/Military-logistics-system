"""INF-H5 -- passwords are hashed by bcrypt directly, with passlib gone.

backend/security.py used to hash through passlib's CryptContext. passlib's
last release is 1.7.4, and it can neither hash nor verify on bcrypt 5.0.0.
requirements.txt held bcrypt at 3.2.2 beneath it. security.py now calls bcrypt
itself.

What must not change is who can log in. The two PASSLIB_* constants below were
written by passlib 1.7.4 on bcrypt 3.2.2, the pair this repository ran before
the change, and are pasted here as that pair produced them. An account row
written before the change holds a value of this kind.

Most of the tests call the two functions in backend/security.py and nothing
else. The last section goes through POST /login and POST /users/, because a
function that answers correctly is not yet a login that does.
"""

import ast
import re
from pathlib import Path

import bcrypt
import pytest
from conftest import create_auth_header
from packaging.requirements import Requirement

from backend import models, security

BACKEND = Path(__file__).resolve().parent.parent / "backend"

SHORT_PASSWORD = "correct horse"
PASSLIB_SHORT_HASH = "$2b$12$74E6ATrl/oW/xqmtQ3V25uz2/S/meBXASahRLnsmY5UA5aI8uLT.e"

# 157 bytes in UTF-8. Every Hebrew letter is two bytes, so the 72-byte cut
# lands by byte count and not by character count.
LONG_PASSWORD = "סיסמה-" * 12 + "tail-that-is-past-the-cut"
PASSLIB_LONG_HASH = "$2b$12$EFDnXc.nJdiiKsn9wmiX8ejyuuR7VxqfSr5nNJVPpB8E32JKtVqCa"

# One bcrypt hash of the same password, cost 4, relabelled with the two older
# prefixes. passlib verified both; a row carrying either still has to.
HASH_2A = "$2a$04$OmxCHHOHK/WRzXkxSmAEgORJkf9Ovn5zcksXfeydw/zKVIl7vEjou"
HASH_2Y = "$2y$04$OmxCHHOHK/WRzXkxSmAEgORJkf9Ovn5zcksXfeydw/zKVIl7vEjou"


# --- hashes written before the change -------------------------------------


def test_a_hash_passlib_wrote_still_verifies():
    assert security.verify_password(SHORT_PASSWORD, PASSLIB_SHORT_HASH) is True


def test_a_wrong_password_does_not_verify_against_a_passlib_hash():
    assert security.verify_password("correct horsf", PASSLIB_SHORT_HASH) is False
    assert security.verify_password("", PASSLIB_SHORT_HASH) is False


@pytest.mark.parametrize(
    "near_miss",
    ["Correct horse", "CORRECT HORSE", "correct horse ", " correct horse", "correct horse\n", "correct  horse"],
)
def test_a_password_is_compared_as_typed(near_miss):
    # No case folding and no trimming, on either path.
    assert security.verify_password(near_miss, PASSLIB_SHORT_HASH) is False
    assert security.verify_password(SHORT_PASSWORD, security.get_password_hash(near_miss)) is False


def test_a_long_password_hashed_by_passlib_still_verifies():
    assert len(LONG_PASSWORD.encode("utf-8")) > security.BCRYPT_MAX_PASSWORD_BYTES
    assert security.verify_password(LONG_PASSWORD, PASSLIB_LONG_HASH) is True


@pytest.mark.parametrize("stored", [HASH_2A, HASH_2Y])
def test_the_older_bcrypt_prefixes_still_verify(stored):
    assert security.verify_password(SHORT_PASSWORD, stored) is True
    assert security.verify_password("correct horsf", stored) is False


# --- what get_password_hash writes -----------------------------------------


def test_a_new_hash_has_the_stored_format():
    hashed = security.get_password_hash(SHORT_PASSWORD)
    assert isinstance(hashed, str)
    assert len(hashed) == len(PASSLIB_SHORT_HASH) == 60
    # Prefix and cost as passlib wrote them.
    assert hashed[:7] == PASSLIB_SHORT_HASH[:7] == "$2b$12$"


def test_a_new_hash_verifies_and_only_for_its_password():
    hashed = security.get_password_hash(SHORT_PASSWORD)
    assert security.verify_password(SHORT_PASSWORD, hashed) is True
    assert security.verify_password("correct horsf", hashed) is False


def test_two_hashes_of_one_password_differ():
    first = security.get_password_hash(SHORT_PASSWORD)
    second = security.get_password_hash(SHORT_PASSWORD)
    assert first != second
    # The salt is the 22 characters after the prefix.
    assert first[7:29] != second[7:29]


def test_bytes_are_accepted_where_passlib_accepted_them():
    hashed = security.get_password_hash(SHORT_PASSWORD.encode("utf-8"))
    assert security.verify_password(SHORT_PASSWORD, hashed) is True
    assert security.verify_password(SHORT_PASSWORD.encode("utf-8"), PASSLIB_SHORT_HASH) is True
    assert security.verify_password(SHORT_PASSWORD, PASSLIB_SHORT_HASH.encode("ascii")) is True


# --- the 72-byte cut ---------------------------------------------------------


def test_the_installed_bcrypt_refuses_what_security_cuts():
    # The reason the cut is written out. If this stops raising, the comment
    # beside BCRYPT_MAX_PASSWORD_BYTES is out of date. Find out what bcrypt
    # then does with the bytes past 72 before touching the cut.
    with pytest.raises(ValueError):
        bcrypt.hashpw(b"x" * 73, bcrypt.gensalt(rounds=4))
    bcrypt.hashpw(b"x" * 72, bcrypt.gensalt(rounds=4))


def test_a_long_password_can_be_hashed_and_verified():
    hashed = security.get_password_hash(LONG_PASSWORD)
    assert security.verify_password(LONG_PASSWORD, hashed) is True


def test_only_the_first_72_bytes_of_a_password_count():
    encoded = LONG_PASSWORD.encode("utf-8")
    for stored in (PASSLIB_LONG_HASH, security.get_password_hash(LONG_PASSWORD)):
        assert security.verify_password(encoded[:72], stored) is True
        assert security.verify_password(encoded[:72] + b"anything", stored) is True
        assert security.verify_password(encoded[:71], stored) is False


def test_the_cut_counts_bytes_and_may_split_a_character():
    # One byte, then two-byte letters: byte 72 is the first half of a letter.
    # bcrypt 3.2.2 cut there, so a cut moved back to a whole character would
    # hash a different password.
    password = "a" + "\u05e1" * 40
    encoded = password.encode("utf-8")
    with pytest.raises(UnicodeDecodeError):
        encoded[:72].decode("utf-8")

    hashed = security.get_password_hash(password)

    assert security.verify_password(password, hashed) is True
    assert bcrypt.checkpw(encoded[:72], hashed.encode("ascii")) is True
    assert bcrypt.checkpw(encoded[:71], hashed.encode("ascii")) is False


def test_a_password_of_exactly_72_bytes_is_not_shortened():
    password = "x" * 71 + "y"
    hashed = security.get_password_hash(password)
    assert security.verify_password(password, hashed) is True
    assert security.verify_password("x" * 71 + "z", hashed) is False


def test_a_null_byte_does_not_end_the_password():
    # passlib refused such a password. bcrypt takes it, and the bytes after
    # the null count.
    hashed = security.get_password_hash("a\x00b")
    assert security.verify_password("a\x00b", hashed) is True
    assert security.verify_password("a", hashed) is False
    assert security.verify_password("a\x00c", hashed) is False


# --- a stored value that is not a hash --------------------------------------


@pytest.mark.parametrize("stored", [None, "", b""])
def test_an_account_without_a_hash_matches_no_password(stored):
    assert security.verify_password(SHORT_PASSWORD, stored) is False
    assert security.verify_password("", stored) is False


@pytest.mark.parametrize(
    "stored",
    [
        "not-a-hash",
        SHORT_PASSWORD,
        PASSLIB_SHORT_HASH[:40],
        PASSLIB_SHORT_HASH + "\n",
        "$2b$99$" + PASSLIB_SHORT_HASH[7:],
        "סיסמה",
    ],
)
def test_a_stored_value_that_is_not_a_bcrypt_hash_matches_no_password(stored):
    # Under passlib each of these raised. The first four were measured on
    # passlib 1.7.4 with bcrypt 3.2.2, the last two on passlib 1.7.4 with its
    # own bcrypt backend.
    assert security.verify_password(SHORT_PASSWORD, stored) is False


def test_the_plain_password_stored_as_the_hash_does_not_verify():
    assert security.verify_password("secret", "secret") is False


def test_a_stored_value_that_cannot_be_encoded_matches_no_password():
    assert security.verify_password(SHORT_PASSWORD, "\ud800") is False


@pytest.mark.parametrize("password", [None, 12345])
def test_a_password_that_is_not_text_is_an_error(password):
    # A caller's mistake, and not an answer about the password.
    with pytest.raises(TypeError):
        security.verify_password(password, PASSLIB_SHORT_HASH)
    with pytest.raises(TypeError):
        security.get_password_hash(password)


def test_a_stored_value_that_is_not_text_is_an_error():
    # Only ValueError means "not a hash". Anything else is a fault and is not
    # turned into a refused login.
    with pytest.raises(TypeError):
        security.verify_password(SHORT_PASSWORD, 12345)


def test_a_password_that_cannot_be_encoded_is_an_error_on_both_paths():
    # A lone surrogate. passlib 1.7.4 raised the same error, before the
    # password reached bcrypt. It is the password that is unusable here, not
    # the stored value, so it is not answered False.
    with pytest.raises(UnicodeEncodeError):
        security.verify_password("\ud800", PASSLIB_SHORT_HASH)
    with pytest.raises(UnicodeEncodeError):
        security.get_password_hash("\ud800")


# --- passlib is gone ---------------------------------------------------------


def _imported_modules(source):
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_import_scan_sees_each_form():
    assert _imported_modules("import passlib") == {"passlib"}
    assert _imported_modules("import os, passlib.hash as h") == {"os", "passlib.hash"}
    assert _imported_modules("def f():\n    from passlib.context import CryptContext") == {"passlib.context"}


def test_nothing_under_backend_imports_passlib():
    files = sorted(BACKEND.rglob("*.py"))
    assert BACKEND / "security.py" in files
    offenders = [
        str(path.relative_to(BACKEND))
        for path in files
        if any(
            name == "passlib" or name.startswith("passlib.")
            for name in _imported_modules(path.read_text(encoding="utf-8"))
        )
    ]
    assert offenders == []


def _requirements(text):
    """Each requirement in a requirements file, without comments or pip options."""
    found = []
    for line in text.replace("\\\n", " ").splitlines():
        line = re.split(r"\s--hash=", line.split("#", 1)[0], maxsplit=1)[0].strip()
        if line and not line.startswith("-"):
            found.append(Requirement(line))
    return found


def test_the_requirements_reader_sees_each_form():
    read = _requirements(
        "fastapi\n# a note\nBcrypt ==3.2.2 ; python_version > '3'\n-r other.txt\n"
        "passlib[bcrypt]>=1  # why\nbcrypt==5.0.0 \\\n    --hash=sha256:ab \\\n    --hash=sha256:cd\n"
    )
    assert [(r.name, str(r.specifier)) for r in read] == [
        ("fastapi", ""),
        ("Bcrypt", "==3.2.2"),
        ("passlib", ">=1"),
        ("bcrypt", "==5.0.0"),
    ]


def test_requirements_name_bcrypt_once_without_passlib_and_do_not_hold_it_below_5():
    read = _requirements((BACKEND.parent / "requirements.txt").read_text(encoding="utf-8"))
    by_name = [r for r in read if r.name.lower() == "bcrypt"]

    assert not [r for r in read if r.name.lower() == "passlib"]
    assert len(by_name) == 1
    # Whatever the line allows, it allows the release this change was made
    # on. The old line, and any other that stays below 5, does not.
    assert by_name[0].specifier.contains("5.0.0")


# --- through the application -------------------------------------------------


def _login(client, username, password):
    return client.post("/login", data={"username": username, "password": password})


def _set_stored_hash(db_session, user, value):
    user.password_hash = value
    db_session.commit()


def test_an_account_whose_hash_passlib_wrote_can_log_in(client, mock_matrix_db, db_session):
    _set_stored_hash(db_session, mock_matrix_db["master"], PASSLIB_SHORT_HASH)

    assert _login(client, "u_master", SHORT_PASSWORD).status_code == 200
    # The fixture password belonged to the hash that was replaced.
    assert _login(client, "u_master", "secret").status_code == 401


def test_an_account_with_a_long_passlib_password_can_log_in(client, mock_matrix_db, db_session):
    _set_stored_hash(db_session, mock_matrix_db["master"], PASSLIB_LONG_HASH)

    assert _login(client, "u_master", LONG_PASSWORD).status_code == 200
    assert _login(client, "u_master", LONG_PASSWORD[:10]).status_code == 401


@pytest.mark.parametrize("stored", [None, "", "not-a-hash", "secret", PASSLIB_SHORT_HASH[:40]])
def test_login_is_refused_when_the_stored_value_is_not_a_hash(client, mock_matrix_db, db_session, stored):
    _set_stored_hash(db_session, mock_matrix_db["master"], stored)

    response = _login(client, "u_master", "secret")

    # Refused as a wrong password is, and in the same words.
    assert response.status_code == 401
    assert response.json() == {"detail": "Incorrect username or password"}
    # The fault is in that row alone.
    assert _login(client, "u_brig_cmdr", "secret").status_code == 200


def test_an_account_made_with_a_long_password_can_log_in(client, mock_matrix_db, group_graph, db_session):
    created = client.post(
        "/users/",
        json={
            "personal_number": "LONG-1",
            "full_name": "Long Password",
            "password": LONG_PASSWORD,
            "group_id": group_graph["188/53/A"].id,
        },
        headers=create_auth_header("u_master"),
    )
    assert created.status_code == 200, created.text

    stored = db_session.query(models.User).filter_by(personal_number="LONG-1").one().password_hash
    assert stored.startswith("$2b$12$") and len(stored) == 60

    assert _login(client, "LONG-1", LONG_PASSWORD).status_code == 200
    assert _login(client, "LONG-1", "wrong").status_code == 401


def test_a_password_holding_a_null_byte_reaches_login_whole(client, mock_matrix_db, db_session):
    _set_stored_hash(db_session, mock_matrix_db["master"], security.get_password_hash("a\x00b"))

    assert _login(client, "u_master", "a\x00b").status_code == 200
    assert _login(client, "u_master", "a").status_code == 401
    assert _login(client, "u_master", "a\x00c").status_code == 401
