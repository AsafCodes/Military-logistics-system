
from fastapi.testclient import TestClient

from backend import models
from tests.conftest import create_auth_header


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
    
    def test_tech_fix_capabilities(
        self, client: TestClient, db_session, mock_matrix_db,
        token_brigade_tech, token_bat_tech, token_company_tech,
    ):
        """A tech may close a fault -- wherever they can see one.

        This test used to assert that all three techs get 200 on TA300, and it
        passed because fix_equipment resolved by raw id: a brigade tech soldier
        who can see nothing whatsoever could repair anything in the force by
        counting upwards. That IS SEC-H6, so the inversion below is the entry
        landing rather than a regression to be worked around.

        All three hold RESOLVE_FAULT over a group containing Company A. What
        separates them is VIEW, which the resolver asks first:

            company_tech_a   holds TA300, and sees its company  -> 200
            bat_tech         sees the battalion, since H1-10    -> 200
            brigade_tech     sees the force, since H1-10        -> 200

        H1-9 left the last two answering 404: they held RESOLVE_FAULT over a
        group whose items they could not see, which is authority no request
        could reach. H1-10 granted the sight to match, so all three now act --
        which is what this test asserted BEFORE H1-9, for entirely the wrong
        reason. It passed then because fix_equipment resolved by raw id and a
        blind account could repair anything in the force by counting. Same
        three 200s, and now every one of them is scoped.

        Refusals are asserted BEFORE the permitted call, because that call
        closes the item's tickets and marks it Functional; running it first
        would leave the two 404s standing on an item the test had just changed.
        """
        item = db_session.query(models.Equipment).filter_by(serial_number="TA300").one()
        item.status = "Malfunctioning"
        db_session.commit()

        for header in (token_brigade_tech, token_bat_tech, token_company_tech):
            item.status = "Malfunctioning"
            db_session.commit()

            res = client.post(f"/maintenance/fix/{item.id}", headers=header)
            assert res.status_code == 200, res.text

            db_session.refresh(item)
            assert item.status == "Functional"

        # And the limit: Company B's tech reaches neither the item nor its unit.
        res = client.post(f"/maintenance/fix/{item.id}", headers=create_auth_header("u_tech_b"))
        assert res.status_code == 404, res.text

    def test_tech_cannot_transfer(
        self, client: TestClient, db_session, mock_matrix_db,
        token_brigade_tech, token_bat_tech, token_company_tech,
    ):
        """Tech Soldiers cannot transfer -- and the refusal comes in two flavours.

        SEC-H3 lived here. The old version posted a NONEXISTENT equipment id and
        asserted 403 three times, which passed for the two techs whose profile
        name was absent from a hardcoded allowlist and failed for the one whose
        name was in it -- "Company Tech Soldier", seeded with the matching
        permission column set false. A string comparison overrode a deliberate
        denial. There is no allowlist now, so the xfail is gone.

        It also cannot go on asserting 403 against a nonexistent id, and the
        reason is the entry's whole shape rather than a detail: the route now
        resolves the item inside the caller's own VIEW extent BEFORE asking
        whether they may act, so an id nobody can see answers 404 and discloses
        nothing. A 403 exists only for a tech who can genuinely see the item.

        So this asserts the sharper claim, against a real one:

            company tech   -- sees TA300, holds no TRANSFER  -> 403
            brigade tech   -- sees it too, since H1-10       -> 403
            battalion tech -- the same                       -> 403

        All three were 403/404/404 until H1-10 granted the techs sight of the
        unit they maintain. The two 404s becoming 403s is that entry landing:
        the refusal moved from the resolver to the gate, which is a STRONGER
        statement of this test's claim rather than a weaker one -- these
        accounts can now see the item perfectly well and still may not move it.

        and, in every case, that the item did not move. The status code is the
        interesting half; the holder is the half that would matter if the gate
        were removed entirely.
        """
        item = db_session.query(models.Equipment).filter_by(serial_number="TA300").one()
        held_by = item.holder_user_id
        payload = {"equipment_id": item.id, "to_holder_id": mock_matrix_db["soldier_b"].id}

        # Sees the item -- they are holding it -- and holds no TRANSFER grant.
        res = client.post("/equipment/transfer", json=payload, headers=token_company_tech)
        assert res.status_code == 403, res.text

        # They see it now, and are refused by the gate rather than the resolver.
        for headers in (token_brigade_tech, token_bat_tech):
            res = client.post("/equipment/transfer", json=payload, headers=headers)
            assert res.status_code == 403, res.text

        db_session.refresh(item)
        assert item.holder_user_id == held_by, "a refused transfer moved the item"

    def test_tech_visibility_matches_the_unit_they_maintain(
        self, client: TestClient, token_brigade_tech, token_bat_tech, token_company_tech
    ):
        """Tech soldiers see the unit they are responsible for, and no further.

        This asserted the opposite until H1-10 -- brigade tech sees nothing,
        company tech sees only the one item it carries -- and it passed because
        this fixture's Company Tech Soldier row set can_view_company_realtime
        FALSE where profiles.py sets it true, and because Brigade Tech Soldier
        has no view flag at all while carrying maintenance authority.

        H1-9 showed what that combination actually produced: under
        404-before-403 the resolver asks VIEW first, so every one of these
        accounts held REPORT_STATUS and RESOLVE_FAULT over groups whose items
        they could not see -- authority no request could reach. H1-10 resolved
        it by granting sight to match, so the rule is now: you see what you may
        maintain.

        Company A's tech gaining SA100 is the visible half of that, and it is
        the mapping profiles.py always specified. Brigade Tech Soldier's root
        grant is the INVENTED half -- see the comment block in conftest -- and
        it is why that account now sees the whole fixture.

        The limit still bites, and that is the other half of the claim:
        Company B's item never appears for Company A's tech.
        """
        def serials(headers):
            res = client.get("/equipment/accessible", headers=headers)
            assert res.status_code == 200
            return {i["serial_number"] for i in res.json()}

        assert serials(token_brigade_tech) == {"SA100", "SB200", "TA300"}
        assert serials(token_bat_tech) == {"SA100", "SB200", "TA300"}
        assert serials(token_company_tech) == {"SA100", "TA300"}

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

    def test_company_tech_cannot_fix_other_company_item(
        self, client: TestClient, db_session, mock_matrix_db, token_company_tech,
    ):
        """Co A's tech cannot repair Co B's item -- and the answer is 404.

        SEC-H6 lived here. fix_equipment resolved SB200 by raw id with an
        existence check, then consulted can_change_maintenance_status, which
        Company Tech Soldier carries; Co A's tech repaired Co B's equipment and
        got 200. The resolver runs first now, so the marker is gone.

        404 rather than 403 is the entire fix, and it is asserted exactly rather
        than as `in [403, 404]`. This account DOES hold RESOLVE_FAULT -- over
        Company A -- so a 403 would confirm that SB200 exists and leave the ids
        of a neighbouring company enumerable one request at a time. Accepting
        either code is also how the original passed while POSTing to a route
        that did not exist.
        """
        item_b = db_session.query(models.Equipment).filter_by(serial_number="SB200").one()
        before = item_b.status

        res = client.post(f"/maintenance/fix/{item_b.id}", headers=token_company_tech)

        assert res.status_code == 404
        db_session.refresh(item_b)
        assert item_b.status == before

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
