"""API-H6: the fault-type approval loop, end to end through the routes.

Each piece already had a test of its own: test_status_authority pins who mints
a pending type, and test_global_authority pins who may read the queue and
approve from it. Nothing walked the whole loop, which is the path the new
/catalog page drives:

    report a new type -> it is pending -> it is in the queue -> approve it
    -> it has left the queue and is listed as approved

Every step goes through a route, never the session, because the defect was a
loop with no way to finish it -- and a step taken through the database would
stand in for a route that might not be there.
"""
from tests.conftest import create_auth_header
from tests.test_global_authority import approve_fault, pending_faults as queue
from tests.test_status_authority import item_named, report

NOVEL = "API-H6-NOVEL"


def listed(client, who):
    """GET /setup/fault_types as a {name: is_pending} map -- what the dropdown reads."""
    res = client.get("/setup/fault_types", headers=create_auth_header(who))
    assert res.status_code == 200
    return {f["name"]: f["is_pending"] for f in res.json()}


def test_a_reported_novel_type_can_be_approved_out_of_the_queue(
    client, db_session, mock_matrix_db
):
    """The whole loop, from a soldier's report to the dropdown.

    u_soldier_a holds SA100 and no grant at all, so their new type is minted
    pending (report_fault's may(REPORT_STATUS) says no). u_brig_cmdr holds
    MANAGE_CATALOG and not MANAGE_PERSONNEL -- the account that needs the
    queue outside /admin, which it cannot reach.
    """
    item = item_named(db_session, "SA100")
    assert report(client, "u_soldier_a", item.id, fault=NOVEL).status_code == 200

    # Pending, and the list route still returns it -- EquipmentPage is what
    # hides it, by reading this flag.
    assert listed(client, "u_soldier_a")[NOVEL] is True

    res = queue(client, "u_brig_cmdr")
    assert res.status_code == 200
    queued = [f for f in res.json() if f["name"] == NOVEL]
    assert len(queued) == 1, res.json()
    assert queued[0]["is_pending"] is True
    # The route has no response_model, so this assertion is the only contract:
    # FaultTypeQueuePage reads it as `FaultType` (frontend/src/types/index.ts),
    # whose three fields are exactly these. id is what the approve URL is built
    # from, so a string id would still "work" right up until it didn't.
    assert set(queued[0]) == {"id", "name", "is_pending"}
    assert isinstance(queued[0]["id"], int)

    assert approve_fault(client, "u_brig_cmdr", queued[0]["id"]).status_code == 200

    # Out of the queue, and approved where the reporter's dropdown looks.
    assert NOVEL not in {f["name"] for f in queue(client, "u_brig_cmdr").json()}
    assert listed(client, "u_soldier_a")[NOVEL] is False


def test_the_queue_is_closed_to_a_status_reporter(client, db_session, mock_matrix_db):
    """The verb that decides who mints pending types does not open the queue.

    u_cmdr_a holds REPORT_STATUS over Company A -- enough that their own new
    types skip review -- and not MANAGE_CATALOG. Skipping review and
    performing it are different authorities: a gate that accepted REPORT_STATUS
    held anywhere would let a company commander approve a type into the
    vocabulary every unit shares.
    """
    item = item_named(db_session, "SA100")
    assert report(client, "u_soldier_a", item.id, fault=NOVEL).status_code == 200
    fault_id = next(
        f["id"] for f in queue(client, "u_brig_cmdr").json() if f["name"] == NOVEL
    )

    assert queue(client, "u_cmdr_a").status_code == 403
    assert approve_fault(client, "u_cmdr_a", fault_id).status_code == 403

    # Still pending: the refusal wrote nothing.
    assert listed(client, "u_soldier_a")[NOVEL] is True
