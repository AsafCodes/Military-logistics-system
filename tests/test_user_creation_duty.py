"""SEC-M2: an account is on duty when it is created, and the caller cannot say otherwise.

The creation body used to carry `is_active_duty`, honoured as sent. Login and
every authenticated route refuse an inactive account, and no route changes the
flag afterwards, so `false` minted an account that could never log in and could
not be activated over HTTP. The field is gone and the route sets True.

The body also refuses any field it does not name. Without that, a caller who
still sends `is_active_duty: false` would get a 200 and an active account, the
opposite of what they asked for, with nothing telling them; and a client that
sends `role` again would get a 200 with no sign it was ignored. (A `role` put
back into the schema itself is a declared field, which `forbid` does not
refuse; the four-field schema test below is what catches that.)
"""
import pytest

from backend import models
from backend.main import app
from tests.conftest import create_auth_header

PASSWORD = "Creation-Secret-7f3a"


def create(client, group_graph, personal_number="DUTY-1", **extra):
    body = {
        "personal_number": personal_number,
        "full_name": "Recruit",
        "password": PASSWORD,
        "group_id": group_graph["188/53/A"].id,
        **extra,
    }
    return client.post("/users/", json=body, headers=create_auth_header("u_master"))


def stored(db_session, personal_number="DUTY-1"):
    return db_session.query(models.User).filter_by(personal_number=personal_number).one_or_none()


# --- the account the route makes --------------------------------------------


def test_a_new_account_is_on_duty(client, db_session, mock_matrix_db, group_graph):
    res = create(client, group_graph)

    assert res.status_code == 200, res.text
    assert res.json()["is_active_duty"] is True
    assert stored(db_session).is_active_duty is True


def test_a_new_account_can_log_in(client, db_session, mock_matrix_db, group_graph):
    """Why the flag matters: login refuses an account that is not on duty."""
    assert create(client, group_graph).status_code == 200

    res = client.post("/login", data={"username": "DUTY-1", "password": PASSWORD})
    assert res.status_code == 200, res.text


# --- fields the body does not name ------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {"is_active_duty": False},
        # Refused whatever its value: the caller does not set this at all.
        {"is_active_duty": True},
        {"role": "master"},
    ],
    ids=["duty_false", "duty_true", "role"],
)
def test_a_field_the_body_does_not_name_is_refused(
    client, db_session, mock_matrix_db, group_graph, extra
):
    res = create(client, group_graph, **extra)

    assert res.status_code == 422, res.text
    [field] = extra
    assert any(
        err["type"] == "extra_forbidden" and err["loc"] == ["body", field]
        for err in res.json()["detail"]
    ), res.text
    assert stored(db_session) is None


def test_the_refusal_does_not_send_the_password_back(
    client, db_session, mock_matrix_db, group_graph
):
    """A 422 carries each error's `input`. For a stray field that is the stray
    value alone; were it the whole body, the password would ride back in it.

    Pinned because the other shape exists: a MISSING field's error carries the
    whole body, password included. That case predates SEC-M2 and is not
    covered here.
    """
    res = create(client, group_graph, is_active_duty=False)

    assert res.status_code == 422
    assert PASSWORD not in res.text


# --- what the published schema says -----------------------------------------


def test_the_schema_names_exactly_four_fields_and_no_others():
    schema = app.openapi()["components"]["schemas"]["UserCreate"]

    assert set(schema["properties"]) == {"personal_number", "full_name", "password", "group_id"}
    assert schema["additionalProperties"] is False


def test_the_response_still_reports_duty_status():
    """Control: the flag left the input, not the output.

    The second assertion pins where `forbid` sits, not a behaviour: on the
    shared UserBase it would break nothing today (every route answering with
    UserResponse returns a row, and pydantic reads only the declared attributes
    off a row, so the row's other columns never count as extras), but it would
    reach every future subclass of UserBase without anyone choosing it.
    """
    schema = app.openapi()["components"]["schemas"]["UserResponse"]

    assert "is_active_duty" in schema["properties"]
    assert "additionalProperties" not in schema
