"""Scoping through the group DAG, exercised end to end at /equipment/accessible.

The four cases below are the ones the materialised-path suite always made, and
they still hold. What changed underneath is the mechanism: an item is visible
when its group lies in extent(user, VIEW), not when its path string happens to
begin with the viewer's. "Foreign" is now a structural fact -- a group with no
edge connecting it to the tree -- rather than a string that fails to match.
"""

from datetime import datetime

from backend import models
from tests.conftest import create_auth_header, create_group


def test_hierarchy_scoping_logic(client, db_session, mock_matrix_db, group_graph):
    """
    Verify that 'get_accessible_equipment' respects group containment.
    """

    # 1. Setup: Create items at different levels of the graph
    # Containment: 188 -> 188/53 -> 188/53/A, 188/53/B

    # A group in no relation to the tree at all. Under the old encoding this
    # was a path that simply failed to share a prefix; here it is a group with
    # no edge, which is the same claim made structurally.
    #
    # create_group, not a bare Unit(): it keeps the closure consistent, without
    # which this group would be isolated for an accidental second reason on top
    # of the intended one. See the helper.
    foreign = create_group(db_session, "920/Other")

    # helper
    def create_item(unique_id, group, holder=None):
        item = models.Equipment(
            catalog_item_id=1,
            status="Functional",
            group_id=group.id,  # The key scoping field
            serial_number=unique_id,
            holder_user_id=holder,
            last_verified_at=datetime.utcnow()
        )
        db_session.add(item)
        return item

    # Items. Held by nobody, so the holder arm cannot account for any of them
    # and every assertion below is about group containment alone.
    create_item("BRIG_ONLY", group_graph["188"])
    create_item("BAT_ONLY", group_graph["188/53"])
    create_item("CO_A_ITEM", group_graph["188/53/A"])
    create_item("CO_B_ITEM", group_graph["188/53/B"])  # Sibling company
    create_item("FOREIGN_ITEM", foreign)  # Unconnected group

    db_session.commit()

    # 2. Test Cases

    # Case A: Brigade Commander, VIEW on the root
    # Should see: everything under 188
    # Should NOT see: FOREIGN
    headers = create_auth_header("u_brig_cmdr")
    res = client.get("/equipment/accessible", headers=headers)
    assert res.status_code == 200
    sns = [i["serial_number"] for i in res.json()]
    assert "BRIG_ONLY" in sns
    assert "BAT_ONLY" in sns
    assert "CO_A_ITEM" in sns
    assert "CO_B_ITEM" in sns
    assert "FOREIGN_ITEM" not in sns

    # Case B: Battalion Commander, VIEW on 188/53
    # Should see: the battalion and both its companies
    # Should NOT see: 188 (an ancestor, not a descendant), FOREIGN
    headers = create_auth_header("u_bat_cmdr")
    res = client.get("/equipment/accessible", headers=headers)
    assert res.status_code == 200
    sns = [i["serial_number"] for i in res.json()]
    assert "BRIG_ONLY" not in sns # Containment points down, not up
    assert "BAT_ONLY" in sns
    assert "CO_A_ITEM" in sns
    assert "CO_B_ITEM" in sns
    assert "FOREIGN_ITEM" not in sns

    # Case C: Company A Commander, VIEW on a leaf
    # Should see: Co A only
    # Should NOT see: 188, 188/53, 188/53/B (Sibling)
    headers = create_auth_header("u_cmdr_a")
    res = client.get("/equipment/accessible", headers=headers)
    assert res.status_code == 200
    sns = [i["serial_number"] for i in res.json()]
    assert "BRIG_ONLY" not in sns
    assert "BAT_ONLY" not in sns
    assert "CO_A_ITEM" in sns
    assert "CO_B_ITEM" not in sns # Sibling isolation check
    assert "FOREIGN_ITEM" not in sns

    # Case D: Soldier, member of Co A but holding no grant over it
    # Should see: ONLY the item they hold (SA100 from conftest)
    # Should NOT see: Co A's other equipment
    headers = create_auth_header("u_soldier_a")
    res = client.get("/equipment/accessible", headers=headers)
    assert res.status_code == 200
    sns = [i["serial_number"] for i in res.json()]

    # Soldier holds 'SA100' in conftest
    assert "SA100" in sns
    # Membership is not authority: being IN Co A conveys no view over it
    assert "CO_A_ITEM" not in sns
