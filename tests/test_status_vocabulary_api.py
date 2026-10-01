"""DATA-H12-2: the API refuses a status it does not know, and reports the ones
it does as bare strings.

H12-1 put the vocabulary on the COLUMNS, so a typo became unwritable -- but it
became unwritable as an IntegrityError raised at commit time, naming a
constraint rather than a caller, from whichever route happened to commit next.
This half moves the refusal to the edge: a request carrying an unknown status is
answered 422 before the route runs, and the two query filters that fed a raw
string into a SQL equality test no longer answer "nothing matched" to a question
the system could not understand.

Two things are asserted here that look like plumbing and are not:

  * THE WIRE FORMAT. Six response fields moved from `str` to a `(str, Enum)`.
    Pydantic's JSON mode emits the member's value, so the payload is unchanged
    -- but `str()` of such a member is "EquipmentStatus.FUNCTIONAL", not
    "Functional", and python-mode `model_dump()` hands back the member itself.
    Nothing in the backend dumps these models in python mode today. That is a
    fact about the current code rather than a guarantee, so the byte-level
    shape is pinned here instead of trusted.

  * 422 VERSUS 200-AND-EMPTY. The distinction the filters now draw is between
    "no such status" and "no such items", and both halves need asserting. A
    route that refused everything would pass the refusal tests alone, which is
    why each is paired with a valid-but-absent value that must still answer
    200 with an empty list.

Deliberately NOT tested here, stated plainly rather than left as a gap: the
literal-to-enum swaps at analytics.py's readiness count, maintenance.py's
"Open"/"Closed", and seed_data.py's five rows have no behavioural signature --
`EquipmentStatus.FUNCTIONAL.value` IS "Functional", so no test can tell the two
spellings apart. What they buy is that the two sides of the readiness
comparison cannot drift, and what guards the enum's own spellings is
test_audit_trail.py, which asserts the bare literals on the columns.
"""

import pytest

from backend import audit_trail, models
from backend.enums import ChangeReason, EquipmentStatus, Sensitivity, TicketStatus

REPORT_FAULT = {"fault_name": "Vocabulary Fault", "description": "will not fire"}


def _item(db_session, serial="SA100"):
    return db_session.query(models.Equipment).filter_by(serial_number=serial).one()


def _open_ticket(client, token, equipment_id):
    response = client.post(
        "/maintenance/report",
        json={"equipment_id": equipment_id, **REPORT_FAULT},
        headers=token,
    )
    assert response.status_code == 200, response.text
    return response.json()["ticket_id"]


# --- 1. The payload did not change shape -------------------------------------

# Every enum now reachable from a response field. A member leaking as its repr
# would put one of these class names into the body, so the blanket check below
# covers fields these tests do not name individually.
LEAKED_REPRS = ("EquipmentStatus.", "TicketStatus.", "ChangeReason.", "Sensitivity.")


def _assert_no_member_repr_leaked(response):
    for marker in LEAKED_REPRS:
        assert marker not in response.text, (
            f"{marker!r} appears in the payload -- a str-mixin member reached "
            f"the serializer as its repr rather than its value: {response.text[:400]}"
        )


def test_equipment_status_is_still_a_bare_string_on_the_wire(
    client, mock_matrix_db, token_master
):
    """EquipmentResponse.status, the field with the most consumers.

    types/index.ts declares this `status: string` and EquipmentPage.tsx
    switches on the literal spellings, so a payload carrying
    "EquipmentStatus.FUNCTIONAL" would not fail here -- it would render an
    unstyled badge in a browser nobody is testing.
    """
    response = client.get("/equipment/accessible", headers=token_master)

    assert response.status_code == 200, response.text
    statuses = {item["status"] for item in response.json()}
    assert statuses == {"Functional"}, (
        f"expected the fixture's bare 'Functional', got {statuses}"
    )
    _assert_no_member_repr_leaked(response)


def test_ticket_status_is_still_a_bare_string_on_the_wire(
    client, mock_matrix_db, db_session, token_master
):
    """TicketResponse.status, written by report_fault as TicketStatus.OPEN.

    Covers both ends of the change at once: the route now assigns the member's
    value rather than a bare "Open" literal, and the schema now types the field
    it comes back through.
    """
    _open_ticket(client, token_master, _item(db_session).id)

    response = client.get("/tickets/", headers=token_master)

    assert response.status_code == 200, response.text
    assert [t["status"] for t in response.json()] == ["Open"]
    _assert_no_member_repr_leaked(response)


def test_the_verification_and_history_payloads_are_still_bare_strings(
    client, mock_matrix_db, db_session, token_company_cmdr
):
    """The remaining four fields, three of them on one history row.

    StatusHistoryResponse carries old_status, new_status AND change_reason, and
    change_reason is the one the H12 plan had excluded -- on the premise that
    the column had no constraint, which H12-1 spent by adding
    ck_equipment_status_history_change_reason in the same batch. Its value is
    lowercase ("verification"), so it would also catch a member reaching the
    wire upper-cased.
    """
    item_id = _item(db_session).id

    created = client.post(
        "/verifications/",
        json={
            "equipment_id": item_id,
            "verification_type": "daily",
            "reported_status": "Malfunctioning",
            "findings": "cracked stock",
            "action_required": True,
        },
        headers=token_company_cmdr,
    )
    assert created.status_code == 200, created.text
    assert created.json()["reported_status"] == "Malfunctioning"
    _assert_no_member_repr_leaked(created)

    listed = client.get(
        f"/verifications/equipment/{item_id}", headers=token_company_cmdr
    )
    assert listed.status_code == 200, listed.text
    assert [v["reported_status"] for v in listed.json()] == ["Malfunctioning"]
    _assert_no_member_repr_leaked(listed)

    history = client.get(f"/equipment/{item_id}/history", headers=token_company_cmdr)
    assert history.status_code == 200, history.text
    row = history.json()[0]
    assert (row["old_status"], row["new_status"]) == ("Functional", "Malfunctioning")
    assert row["change_reason"] == "verification"
    _assert_no_member_repr_leaked(history)


# --- 2. The two filters: "no such status" is not "no such items" -------------

# Each list ends with the OTHER route's vocabulary, which is the sharpest case:
# "Open" is a perfectly good status somewhere in this system and still means
# nothing to the inventory report. A shared free-text filter would take it.
INVENTORY_JUNK = ["Functinoal", "FUNCTIONAL", "functional", "BANANA", "", "Open"]
TICKET_JUNK = ["Opne", "OPEN", "open", "BANANA", "", "Functional"]


@pytest.mark.parametrize("junk", INVENTORY_JUNK)
def test_the_inventory_report_refuses_a_status_outside_the_vocabulary(
    client, mock_matrix_db, token_master, junk
):
    """422, where this used to be 200 with an empty list.

    The caller could not tell "nothing is broken" from "you misspelled the
    filter" -- the same silent-typo failure DATA-H12 is about, read side.

    `""` is in the list as a documented behaviour change rather than an
    oversight: it used to be falsy and SKIP the filter, returning everything,
    and is now refused like any other non-member. Nothing in the repo sends it
    -- reports.service.ts omits the key when the filter is unset.

    "FUNCTIONAL" is the member's NAME and must be refused too: Pydantic matches
    a str-mixin enum by VALUE, and a caller reading the Python source rather
    than the API would guess wrong and has to be told.
    """
    response = client.get(
        "/reports/query", params={"status": junk}, headers=token_master
    )

    assert response.status_code == 422, (
        f"{junk!r} was accepted with {response.status_code} -- the status "
        f"filter is not constraining the vocabulary: {response.text[:300]}"
    )


@pytest.mark.parametrize("junk", TICKET_JUNK)
def test_the_ticket_list_refuses_a_status_outside_the_vocabulary(
    client, mock_matrix_db, token_master, junk
):
    """The sibling filter, same change, separate vocabulary (TicketStatus)."""
    response = client.get(
        "/tickets/", params={"status_filter": junk}, headers=token_master
    )

    assert response.status_code == 422, (
        f"{junk!r} was accepted with {response.status_code} -- the ticket "
        f"status filter is not constraining the vocabulary: {response.text[:300]}"
    )


def test_a_valid_inventory_status_still_filters(
    client, mock_matrix_db, token_master
):
    """The positive control, without which the refusal test above proves nothing.

    A route that answered 422 to everything would pass the parametrized test
    and be useless. Both arms matter: a member that MATCHES returns rows, and a
    member that matches NOTHING returns 200 with an empty list -- that is the
    distinction the 422 exists to draw.
    """
    matching = client.get(
        "/reports/query",
        params={"status": EquipmentStatus.FUNCTIONAL.value},
        headers=token_master,
    )
    assert matching.status_code == 200, matching.text
    assert len(matching.json()) == 3, (
        f"the fixture's three Functional items did not come back: {matching.json()}"
    )

    absent = client.get(
        "/reports/query",
        params={"status": EquipmentStatus.MISSING.value},
        headers=token_master,
    )
    assert absent.status_code == 200, absent.text
    assert absent.json() == [], (
        "a valid status matching no row must answer 200 with an empty list, "
        "not 422 -- 'no such items' is a different answer from 'no such status'"
    )


def test_a_valid_ticket_status_still_filters(
    client, mock_matrix_db, db_session, token_master
):
    """Same pairing for the ticket list, plus the member no writer assigns.

    IN_PROGRESS is accepted and answers empty on purpose. No backend path
    assigns it -- report_fault writes OPEN and fix_equipment writes CLOSED --
    so the emptiness is a true statement about the queue rather than a lie
    about the query, and DATA-M22 is what gives it a writer. MaintenancePage.tsx
    already renders the tab.
    """
    _open_ticket(client, token_master, _item(db_session).id)

    open_tickets = client.get(
        "/tickets/",
        params={"status_filter": TicketStatus.OPEN.value},
        headers=token_master,
    )
    assert open_tickets.status_code == 200, open_tickets.text
    assert [t["status"] for t in open_tickets.json()] == ["Open"]

    for unwritten in (TicketStatus.CLOSED, TicketStatus.IN_PROGRESS):
        response = client.get(
            "/tickets/",
            params={"status_filter": unwritten.value},
            headers=token_master,
        )
        assert response.status_code == 200, response.text
        assert response.json() == [], (
            f"{unwritten.value!r} is in the vocabulary and must be accepted "
            "even though nothing writes it"
        )


def test_an_omitted_filter_still_means_no_filter(
    client, mock_matrix_db, db_session, token_master
):
    """Optional stayed Optional. The change was to the VALUES, not to presence.

    Worth its own test because the obvious way to get a 422 on `?status=` --
    making the parameter required -- would break every caller in the repo,
    none of which send it.
    """
    _open_ticket(client, token_master, _item(db_session).id)

    assert len(client.get("/reports/query", headers=token_master).json()) == 3
    assert len(client.get("/tickets/", headers=token_master).json()) == 1


# --- 3. The annotations are enforcement, not decoration ----------------------


@pytest.mark.parametrize(
    "setter, kwarg, junk, accepted",
    [
        ("set_status", "new_status", "Functinoal", "Functional"),
        ("set_status", "new_status", "FUNCTIONAL", "Functional"),
        ("set_sensitivity", "new_sensitivity", "classified", "CLASSIFIED"),
        ("set_sensitivity", "new_sensitivity", "TOP_SECRET", "CLASSIFIED"),
    ],
)
def test_the_setters_refuse_a_value_outside_their_enum(
    db_session, mock_matrix_db, setter, kwarg, junk, accepted
):
    """Python does not check annotations, so this is the line that makes them true.

    That is this module's own founding complaint -- "a module plus a convention
    is not that" -- turned on its own signatures. Before H12-1 a junk value
    here wrote silently and corrupted the readiness count; between H12-1 and
    this ticket it reached the database and came back as an IntegrityError at
    commit time, naming a constraint rather than the caller that caused it.

    The message must name the accepted values: the caller is a programmer who
    passed the wrong vocabulary, and the fix is to know which one was wanted.
    """
    item = _item(db_session)
    kwargs = {"equipment": item, "actor": mock_matrix_db["master"], kwarg: junk}
    if setter == "set_status":
        kwargs["reason"] = ChangeReason.VERIFICATION

    with pytest.raises(ValueError, match=junk) as raised:
        getattr(audit_trail, setter)(db_session, **kwargs)

    assert accepted in str(raised.value), (
        f"the refusal did not name the accepted values: {raised.value}"
    )
    db_session.rollback()
    assert _item(db_session).status == "Functional", "the junk value was written"


@pytest.mark.parametrize("empty", ["", None])
def test_emptiness_is_still_refused_as_emptiness(db_session, mock_matrix_db, empty):
    """The guard order, which is load-bearing and easy to lose.

    An empty new_status is not in the vocabulary either, so the coercion would
    also refuse it -- with a message about which statuses are accepted, when
    the actual fault is that the caller passed nothing. The emptiness guards
    run FIRST and keep their own diagnosis. Moving the coercion above them
    passes every other test in this file.
    """
    item = _item(db_session)

    with pytest.raises(ValueError, match="non-empty new_status"):
        audit_trail.set_status(
            db_session,
            equipment=item,
            actor=mock_matrix_db["master"],
            new_status=empty,
            reason=ChangeReason.VERIFICATION,
        )

    with pytest.raises(ValueError, match="non-empty new_sensitivity"):
        audit_trail.set_sensitivity(
            db_session,
            equipment=item,
            actor=mock_matrix_db["master"],
            new_sensitivity=empty,
        )


@pytest.mark.parametrize(
    "supplied", [EquipmentStatus.MISSING, "Missing"], ids=["member", "string"]
)
def test_the_column_receives_the_value_whichever_spelling_arrived(
    db_session, mock_matrix_db, supplied
):
    """Normalisation, and the reason it is not cosmetic.

    `str()` of a str-mixin member is "EquipmentStatus.MISSING", so a member
    that reached an f-string or a driver that stringifies its parameters would
    write a value no reader can match and no CHECK admits. The setters coerce
    to `.value`, so the column receives exactly what it received before this
    ticket regardless of which spelling the caller held.

    Asserted on the COLUMN rather than through a response, because a response
    would re-serialise through the enum and hide the difference.
    """
    item = _item(db_session)

    audit_trail.set_status(
        db_session,
        equipment=item,
        actor=mock_matrix_db["master"],
        new_status=supplied,
        reason=ChangeReason.VERIFICATION,
    )
    db_session.commit()

    stored = _item(db_session).status
    assert stored == "Missing", f"the column holds {stored!r}"
    assert type(stored) is str, (
        f"the column round-tripped as {type(stored)} -- a member was stored "
        "rather than its value"
    )

    history = (
        db_session.query(models.EquipmentStatusHistory)
        .filter_by(equipment_id=item.id)
        .one()
    )
    assert (history.old_status, history.new_status) == ("Functional", "Missing")
    assert type(history.new_status) is str


def test_set_sensitivity_normalises_too(db_session, mock_matrix_db):
    """The same coercion on the classification side, where the cost differs.

    scope_equipment_query matches classification with is_distinct_from, under
    which ANY junk string is TRUE against CLASSIFIED -- so a value this setter
    accepted but no reader recognises makes the item visible to callers holding
    no VIEW_CLASSIFIED. That is a silent widening of access, not a display bug,
    which is why the coercion is here and not only in the request schema.
    """
    item = _item(db_session)

    audit_trail.set_sensitivity(
        db_session,
        equipment=item,
        actor=mock_matrix_db["master"],
        new_sensitivity=Sensitivity.CLASSIFIED,
    )
    db_session.commit()

    stored = _item(db_session).sensitivity
    assert stored == "CLASSIFIED", f"the column holds {stored!r}"
    assert type(stored) is str


# --- 4. Each response field is pinned on its own ----------------------------
#
# These exist because the H12-1 constraints MASK the schema typing: with the
# column refusing a junk value, there is no way to build a corrupt row and so
# no way to observe the response failing closed on one. Deleting
# `status: EquipmentStatus` from EquipmentResponse turns nothing red in
# sections 1-3 above -- section 1 asserts the payload says "Functional", which
# a bare `str` field reports just as happily.
#
# That is the DATA-H7 shape, a second layer quietly weakening the tests for the
# first, and test_sensitivity_contract.py already met it at H12-1 and split its
# test in two for exactly this reason. Same remedy here, extended to the five
# fields H12-2 added: assert the schema directly, with no database involved.
#
# Defence in depth is only depth while both layers are independently pinned.

VALID_RESPONSE_KWARGS = {
    "EquipmentResponse": dict(
        id=1,
        type="Rifle",
        serial_number="SA100",
        status="Functional",
        holder_user_id=1,
        custom_location=None,
        actual_location_id=None,
        item_name="Rifle",
        current_state_description="Functional",
        compliance_level="GOOD",
        report_status="Reported",
        compliance_check="GOOD",
    ),
    "TicketResponse": dict(
        id=1,
        equipment_id=1,
        fault_type_id=1,
        equipment_name="Rifle",
        fault_type="Jam",
        status="Open",
        description="will not fire",
    ),
    "VerificationResponse": dict(
        id=1,
        equipment_id=1,
        verification_type="daily",
        reported_status="Functional",
        findings=None,
        action_required=False,
        created_date="2026-01-01T00:00:00Z",
        created_by=1,
    ),
    "StatusHistoryResponse": dict(
        id=1,
        equipment_id=1,
        old_status="Functional",
        new_status="Malfunctioning",
        change_reason="verification",
        verification_id=None,
        notes=None,
        created_date="2026-01-01T00:00:00Z",
        created_by=1,
    ),
}

# model, field, a junk value, and a value from the WRONG enum. The second is
# the one that would survive a copy-paste error: typing new_status to
# TicketStatus rather than EquipmentStatus is a plausible slip, and only a
# cross-vocabulary case catches it.
VOCABULARY_FIELDS = [
    ("EquipmentResponse", "status", "Functinoal", "Open"),
    ("TicketResponse", "status", "Opne", "Functional"),
    ("VerificationResponse", "reported_status", "Functinoal", "Open"),
    ("StatusHistoryResponse", "old_status", "Functinoal", "Open"),
    ("StatusHistoryResponse", "new_status", "Malfunctionin", "Closed"),
    ("StatusHistoryResponse", "change_reason", "verifcation", "Functional"),
    # Predates this ticket (DATA-H3-1) and is covered by
    # test_sensitivity_contract.py as well. Carried here anyway, so that the
    # response side of the vocabulary is pinned in ONE place with complete
    # kwargs and a positive control: that other test passes its model 9 fewer
    # required fields than the model has, so it discriminates only as long as
    # Pydantic keeps truncating the `input_value` repr before reaching this
    # key. Verified by mutation that it does still discriminate today -- this
    # is insurance against a fragile pin, not a replacement for a broken one.
    ("EquipmentResponse", "sensitivity", "BANANA", "Open"),
]


@pytest.mark.parametrize(
    "model_name, field, junk, wrong_vocabulary",
    VOCABULARY_FIELDS,
    ids=[f"{m}.{f}" for m, f, _, _ in VOCABULARY_FIELDS],
)
def test_the_response_schema_refuses_a_junk_value_on_its_own(
    model_name, field, junk, wrong_vocabulary
):
    """No database, no route: the schema alone must fail closed.

    Fail-CLOSED is the right direction for a status field for the reason
    DATA-H12 states as its "why it matters": the readiness metric counts one
    exact literal, so a value no reader recognises is not a cosmetic blemish
    but a silently wrong number. Raising makes it a visible 500; a bare `str`
    field would report the junk and let the count quietly drop.

    The valid construction below is a positive control for the whole
    parametrization -- if a model gains a required field, every case here would
    otherwise "pass" on an unrelated missing-field error.
    """
    from pydantic import ValidationError

    from backend import schemas

    model = getattr(schemas, model_name)
    valid = VALID_RESPONSE_KWARGS[model_name]

    model(**valid)  # positive control: these kwargs are complete and in-vocabulary

    for refused in (junk, wrong_vocabulary):
        with pytest.raises(ValidationError) as excinfo:
            model(**{**valid, field: refused})

        assert field in str(excinfo.value), (
            f"{model_name}.{field} accepted {refused!r} -- the field is a bare "
            f"str again, so an out-of-vocabulary row would be reported as "
            f"though it were valid: {excinfo.value}"
        )


# The request-side fields. Enum-typed before this ticket and pinned by their
# own routes' 422 tests (test_sensitivity_contract.py sections 2 and 4), so they
# are declared here only so that the walk below can be exhaustive -- the roster
# has to know about every vocabulary field in the file to be able to report a
# new one.
PINNED_ELSEWHERE = {
    ("EquipmentCreate", "sensitivity"),
    ("SetSensitivityRequest", "sensitivity"),
    ("VerificationCreate", "reported_status"),
}


def test_every_vocabulary_typed_field_in_the_file_is_accounted_for():
    """A roster, so an eighth field cannot arrive unpinned.

    The list above is hand-written and a hand-written list goes stale. This
    walks EVERY Pydantic model in schemas.py -- not just the four this file
    constructs -- and fails if any of them carries a vocabulary-typed field
    that neither the parametrization above nor PINNED_ELSEWHERE names.

    Walking the whole module rather than a known list is the entire point. The
    failure mode this guards is precisely how VerificationResponse.
    reported_status sat as a bare `str` for a whole ticket while the REQUEST
    field beside it was already an enum: nobody was looking at the file as a
    whole. A roster that iterated only the models already under test would have
    reproduced that blind spot rather than closed it.
    """
    import inspect

    from pydantic import BaseModel

    from backend import schemas

    vocabularies = (EquipmentStatus, TicketStatus, ChangeReason, Sensitivity)
    found = set()
    for model_name, obj in vars(schemas).items():
        if not (
            inspect.isclass(obj)
            and issubclass(obj, BaseModel)
            and obj is not BaseModel
        ):
            continue
        for name, field in obj.model_fields.items():
            annotation = field.annotation
            # Optional[X] keeps its member in __args__; a bare X does not.
            candidates = getattr(annotation, "__args__", (annotation,))
            if any(c in vocabularies for c in candidates):
                found.add((model_name, name))

    pinned = {(m, f) for m, f, _, _ in VOCABULARY_FIELDS} | PINNED_ELSEWHERE

    assert found == pinned, (
        f"unpinned vocabulary fields: {sorted(found - pinned)}; "
        f"pinned but gone: {sorted(pinned - found)}"
    )
