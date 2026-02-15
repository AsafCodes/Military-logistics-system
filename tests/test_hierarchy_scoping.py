
import pytest
from tests.conftest import create_auth_header
import models
from datetime import datetime

def test_hierarchy_scoping_logic(client, db_session, mock_matrix_db):
    """
    Verify that the 'get_accessible_equipment' endpoint respects the Defensive Materialized Path logic.
    """
    
    # 1. Setup: Create items at different levels of hierarchy
    # Hierarchy: 188 -> 53 -> A
    
    # helper
    def create_item(unique_id, hierarchy, holder=None):
        item = models.Equipment(
            catalog_item_id=1,
            status="Functional",
            unit_hierarchy=hierarchy, # The key scoping field
            serial_number=unique_id,
            holder_user_id=holder,
            last_verified_at=datetime.utcnow()
        )
        db_session.add(item)
        return item

    # Items
    item_brigade = create_item("BRIG_ONLY", "188")
    item_battalion = create_item("BAT_ONLY", "188/53")
    item_company_a = create_item("CO_A_ITEM", "188/53/A")
    item_company_b = create_item("CO_B_ITEM", "188/53/B") # Sibling company
    item_foreign = create_item("FOREIGN_ITEM", "920/Other") # Totally different unit
    
    db_session.commit()
    
    # 2. Test Cases
    
    # Case A: Brigade Commander (188) SCPE: 188%
    # Should see: 188, 188/53, 188/53/A, 188/53/B
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

    # Case B: Battalion Commander (188/53) SCOPE: 188/53%
    # Should see: 188/53, 188/53/A, 188/53/B
    # Should NOT see: 188 (Parent), FOREIGN
    headers = create_auth_header("u_bat_cmdr")
    res = client.get("/equipment/accessible", headers=headers)
    assert res.status_code == 200
    sns = [i["serial_number"] for i in res.json()]
    assert "BRIG_ONLY" not in sns # Cannot see parent
    assert "BAT_ONLY" in sns
    assert "CO_A_ITEM" in sns
    assert "CO_B_ITEM" in sns
    assert "FOREIGN_ITEM" not in sns

    # Case C: Company A Commander (188/53/A) SCOPE: 188/53/A%
    # Should see: 188/53/A
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

    # Case D: Soldier (No Hierarchy) SCOPE: Personal only
    # Should see: ONLY item held by them (SA100 from conftest)
    # Should NOT see: Any general unit items
    headers = create_auth_header("u_soldier_a")
    res = client.get("/equipment/accessible", headers=headers)
    assert res.status_code == 200
    sns = [i["serial_number"] for i in res.json()]
    
    # Soldier holds 'SA100' in conftest
    assert "SA100" in sns 
    assert "CO_A_ITEM" not in sns # Even if in same unit, if no permission, can't see generic
