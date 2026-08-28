"""SEC-H8: a discharged account should not hold a credential at all.

Two halves, and the second is the one that matters. Three routes depended on
`get_current_user` rather than `get_current_active_user`, so a deactivated
account kept full access to them — including the verification and status-history
reads, which is the audit trail.

Switching those three closes the instances. It does not close the class: the
per-route check only refuses a token that was already issued, so every future
route that forgets the active-duty dependency reopens it. Refusing at login is
what makes the omission harmless, and it is why both halves are here.
"""
import pytest

from backend import models
from tests.conftest import create_auth_header


def login(client, personal_number, password="secret"):
    return client.post(
        "/login",
        data={"username": personal_number, "password": password},
    )


@pytest.fixture
def discharged(db_session, mock_matrix_db):
    """soldier_a, deactivated after their token was minted.

    The order matters and is the whole scenario: `create_auth_header` signs a
    token from the personal number alone, so a header taken before deactivation
    stays cryptographically valid afterwards. That is exactly the real case —
    somebody is discharged while holding a live session.
    """
    header = create_auth_header("u_soldier_a")
    user = mock_matrix_db["soldier_a"]
    user.is_active_duty = False
    db_session.commit()
    return header, user


# --- the door ---------------------------------------------------------------


def test_a_discharged_account_cannot_obtain_a_token(client, db_session, mock_matrix_db):
    """The half that closes the class rather than the instances.

    Per-route refusal still ISSUES the credential and declines it afterwards, so
    the system is one forgotten dependency away from the leak returning. This
    refuses at the door, so there is nothing to forget.
    """
    assert login(client, "u_soldier_a").status_code == 200, "premise: they can log in"

    mock_matrix_db["soldier_a"].is_active_duty = False
    db_session.commit()

    assert login(client, "u_soldier_a").status_code == 401


def test_the_refusal_does_not_reveal_that_the_account_exists(
    client, db_session, mock_matrix_db
):
    """Same code, same wording, as a wrong password.

    A distinct status or message would turn the login form into an oracle for
    which personal numbers exist and have been deactivated — and a personal
    number here is a military ID, which is short and enumerable. The uniformity
    is deliberate, so it is asserted rather than left to be tidied away by
    someone improving the error messages.
    """
    mock_matrix_db["soldier_a"].is_active_duty = False
    db_session.commit()

    discharged_res = login(client, "u_soldier_a")
    wrong_password = login(client, "u_soldier_b", password="not-the-password")
    no_such_user = login(client, "u_does_not_exist")

    assert discharged_res.status_code == wrong_password.status_code == 401
    assert discharged_res.json() == wrong_password.json() == no_such_user.json()


# --- the routes that were reachable with a live token -----------------------


@pytest.mark.parametrize(
    "path",
    [
        "/verifications/equipment/{id}",
        "/equipment/{id}/history",
    ],
    ids=["verification_history", "status_history"],
)
def test_the_audit_reads_refuse_a_deactivated_holder(
    client, db_session, discharged, path
):
    """The two routes that were on the plain resolver.

    They were the last ones, and the pairing is unlucky: the audit trail is
    precisely what a discharged account should not still be reading, and it was
    the only thing they could still reach.

    Parametrised over both because they were changed in one edit and would
    regress in one edit.
    """
    header, _ = discharged
    item = db_session.query(models.Equipment).filter_by(serial_number="SA100").one()

    res = client.get(path.format(id=item.id), headers=header)
    assert res.status_code == 400, res.text


def test_an_active_holder_still_reads_the_same_routes(
    client, db_session, mock_matrix_db
):
    """The control, so the test above is not passing because the routes broke.

    Same user, same item, still on duty.
    """
    item = db_session.query(models.Equipment).filter_by(serial_number="SA100").one()
    header = create_auth_header("u_soldier_a")

    assert client.get(f"/verifications/equipment/{item.id}", headers=header).status_code == 200
    assert client.get(f"/equipment/{item.id}/history", headers=header).status_code == 200
