
from fastapi.testclient import TestClient

class TestRBACMatrix:
    """
    Comprehensive Role-Based Access Control Matrix Test.
    Verifies the Strict Hierarchy & Functionality separation:
    - Commanders: See All, Move All.
    - Tech Soldiers: Fix Only, No Transfer, Limited Visibility.
    """

    # ==========================================
    # SECTION A: The "Fixer" Limit (Tech Soldiers)
    # ==========================================
    
    def test_tech_fix_capabilities(self, client: TestClient, token_brigade_tech, token_bat_tech, token_company_tech):
        """Verify ANY Tech Soldier (Brigade/Bat/Co) CAN fix an item."""
        # Find an assigned item to fix (e.g., SA100 from seeding)
        # In a real scenario, we'd query to get the ID, but we know the seed logic.
        # Let's assume we find an item ID dynamically or use a known one if seeding is deterministic.
        # Based on conftest, SA100 is assigned to Soldier A.
        # We need to get the item ID first.
        
        # Helper to get an item ID
        res = client.get("/equipment/accessible", headers=token_company_tech) # He sees his own item TA300
        assert res.status_code == 200
        my_item_id = res.json()[0]["id"]
        
        # 1. Brigade Tech fixes
        res = client.post(f"/maintenance/fix/{my_item_id}", json={"equipment_id": my_item_id, "notes": "Fixed by Brigade Tech"}, headers=token_brigade_tech)
        assert res.status_code == 200
        
        # 2. Battalion Tech fixes
        res = client.post(f"/maintenance/fix/{my_item_id}", json={"equipment_id": my_item_id, "notes": "Fixed by Bat Tech"}, headers=token_bat_tech)
        assert res.status_code == 200

        # 3. Company Tech fixes
        res = client.post(f"/maintenance/fix/{my_item_id}", json={"equipment_id": my_item_id, "notes": "Fixed by Co Tech"}, headers=token_company_tech)
        assert res.status_code == 200

    def test_tech_cannot_transfer(self, client: TestClient, token_brigade_tech, token_bat_tech, token_company_tech):
        """Verify Tech Soldiers CANNOT transfer items (403 Forbidden)."""
        # Techs usually have their own items or can see some items. 
        # Even if they try to transfer their OWN item, it should be forbidden if policy says "Only Commanders Move".
        
        # Get an item ID that exists (e.g., "TA300" held by company_tech_a)
        # We will try to transfer it to someone else.
        
        transfer_payload = {"equipment_id": 999, "to_holder_id": 1} # IDs don't matter for 403 check usually, but good to be safe
        
        # 1. Brigade Tech
        res = client.post("/equipment/transfer", json=transfer_payload, headers=token_brigade_tech)
        assert res.status_code == 403
        
        # 2. Battalion Tech
        res = client.post("/equipment/transfer", json=transfer_payload, headers=token_bat_tech)
        assert res.status_code == 403
        
        # 3. Company Tech
        res = client.post("/equipment/transfer", json=transfer_payload, headers=token_company_tech)
        assert res.status_code == 403

    def test_tech_limited_visibility(self, client: TestClient, token_brigade_tech, token_bat_tech, token_company_tech):
        """
        Verify Tech Soldiers DO NOT see unit inventory.
        GET /equipment/accessible should return ONLY their personal items.
        """
        # Brigade Tech should NOT see everything in Brigade.
        res = client.get("/equipment/accessible", headers=token_brigade_tech)
        assert res.status_code == 200
        items = res.json()
        # He should see at most his own items (if assigned) or empty.
        # In seeding, 'u_brig_tech' has NO items assigned.
        assert len(items) == 0 

        # Company Tech A has 1 item (TA300). He should see ONLY that.
        # He should NOT see Soldier A's item (SA100).
        res = client.get("/equipment/accessible", headers=token_company_tech)
        assert res.status_code == 200
        items = res.json()
        assert len(items) == 1
        assert items[0]["serial_number"] == "TA300"

    # ==========================================
    # SECTION B: The "Commander" Privilege
    # ==========================================

    def test_commanders_full_visibility(self, client: TestClient, token_brigade_cmdr, token_bat_cmdr, token_company_cmdr):
        """
        Verify Commanders SEE hierarchy:
        - Brigade Cmdr: Sees All (Company A + Company B items).
        - Battalion Cmdr: Sees All (Company A + Company B items).
        - Company Cmdr: Sees Only Own Company.
        """
        # 1. Brigade Commander (Top of hierarchy)
        res = client.get("/equipment/accessible", headers=token_brigade_cmdr)
        assert res.status_code == 200
        items = res.json()
        sns = [i["serial_number"] for i in items]
        # Must see items from Co A and Co B
        assert "SA100" in sns # Co A
        assert "SB200" in sns # Co B

        # 2. Battalion Commander (Middle)
        res = client.get("/equipment/accessible", headers=token_bat_cmdr)
        assert res.status_code == 200
        items = res.json()
        sns = [i["serial_number"] for i in items]
        assert "SA100" in sns
        assert "SB200" in sns

        # 3. Company Commander A (Bottom)
        res = client.get("/equipment/accessible", headers=token_company_cmdr)
        assert res.status_code == 200
        items = res.json()
        sns = [i["serial_number"] for i in items]
        assert "SA100" in sns # Sees his soldier's item
        assert "TA300" in sns # Sees his tech's item
        assert "SB200" not in sns # CANNOT see Company B

    def test_commanders_can_transfer(self, client: TestClient, token_bat_cmdr, token_company_cmdr):
        """Verify Commanders CAN transfer items."""
        # Need actual IDs. 
        # Admin gets Co A item
        res = client.get("/equipment/accessible", headers=token_company_cmdr)
        print(f"DEBUG: Company Cmdr items: {res.json()}")
        item_a = next(i for i in res.json() if i["serial_number"] == "SA100")
        
        # Transfer SA100 from Soldier A to Tech A (Internal Company Move)
        # We need Tech A's ID. In seeding: 'u_tech_a'.
        # We can't easily get user IDs from API unless we have a user list endpoint or helper.
        # But we can transfer to SELF for the test, or rely on knowing the seed logic if we really want.
        # Better: Transfer to the Commander himself (Company Cmdr A).
        
        my_res = client.get("/users/me", headers=token_company_cmdr)
        my_id = my_res.json()["id"]

        payload = {"equipment_id": item_a["id"], "to_holder_id": my_id}
        
        # 1. Company Commander Performs Transfer
        res = client.post("/equipment/transfer", json=payload, headers=token_company_cmdr)
        assert res.status_code == 200
        assert res.json()["status"] == "Transferred"

    # ==========================================
    # SECTION C: Hierarchy Enforcement
    # ==========================================

    def test_company_tech_cannot_fix_other_company_item(self, client: TestClient, token_company_tech, token_bat_cmdr):
        """
        Verify Co A Tech cannot fix Co B Item (SB200).
        (Assuming strict cross-unit logic exists in 'fix' endpoint).
        """
        # Get Co B item ID (SB200) - Bat Cmdr can see it
        res = client.get("/equipment/accessible", headers=token_bat_cmdr)
        item_b = next(i for i in res.json() if i["serial_number"] == "SB200")
        
        # Co A Tech tries to fix it
        res = client.post("/maintenance/fix", json={"equipment_id": item_b["id"], "notes": "Hacking!"}, headers=token_company_tech)
        
        # Depending on implementation, this might be 404 (Not Found in scope) or 403 (Forbidden)
        # If the query filters by hierarchy, it won't be found.
        assert res.status_code in [403, 404]

    # ==========================================
    # SECTION D: Master
    # ==========================================

    def test_master_is_admin_only(self, client: TestClient, token_master):
        """Verify Master is pure Admin (Users) and DOES NOT participate in Logistics."""
        # 1. Can manage users
        res = client.get("/users", headers=token_master)
        assert res.status_code == 200
        
        # 2. Cannot Transfer items (Not a commander in the field) - OR - 
        # Business Rule: "Master ... DO NOT hold equipment."
        # Verify he has no PERSONAL equipment
        res = client.get("/users/me/equipment", headers=token_master)
        assert res.status_code == 200
        assert len(res.json()) == 0 # Should be empty
        
        # Verify he SEES everything (Admin View)
        res = client.get("/equipment/accessible", headers=token_master)
        assert res.status_code == 200
        assert len(res.json()) > 0 # He sees the matrix
