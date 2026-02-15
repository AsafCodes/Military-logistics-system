
import pytest
from fastapi.testclient import TestClient
import pandas as pd
from io import BytesIO

class TestExportHierarchy:
    """
    Comprehensive Export Feature Tests (Excel).
    Verifies that 'GET /reports/export/daily_activity' respects Strict Hierarchy.
    """

    # ==========================================
    # Group A: Company Scope (Restricted)
    # ==========================================

    def test_company_cmdr_scope(self, client: TestClient, token_company_cmdr, seed_operational_logs):
        """
        Company Commander (Co A):
        - MUST see: Log_CoA
        - MUST NOT see: Log_CoB, Log_Bat1_HQ, Log_Bat2_Foreign
        """
        res = client.get("/reports/export/daily_activity?file_format=excel", headers=token_company_cmdr)
        assert res.status_code == 200
        
        # Parse Excel
        df = pd.read_excel(BytesIO(res.content))
        events = df['Event'].tolist()
        
        # Verify Content
        assert "Log_CoA" in events
        assert "Log_CoB" not in events
        assert "Log_Bat1_HQ" not in events
        assert "Log_Bat2_Foreign" not in events
        
        # Verify Count (Should be exactly 1 based on seed)
        assert len(df) == 1

    def test_company_tech_scope(self, client: TestClient, token_company_tech, seed_operational_logs):
        """
        Company Tech (Co A): Same restricted scope as Commander.
        """
        res = client.get("/reports/export/daily_activity?file_format=excel", headers=token_company_tech)
        assert res.status_code == 200
        
        df = pd.read_excel(BytesIO(res.content))
        events = df['Event'].tolist()
        
        assert "Log_CoA" in events
        assert "Log_CoB" not in events
        assert len(df) == 1

    # ==========================================
    # Group B: Battalion Scope (Intermediate)
    # ==========================================

    def test_bat_cmdr_scope(self, client: TestClient, token_bat_cmdr, seed_operational_logs):
        """
        Battalion Commander (Bat 1):
        - MUST see: Log_CoA, Log_CoB, Log_Bat1_HQ (All under Bat 1)
        - MUST NOT see: Log_Bat2_Foreign
        """
        res = client.get("/reports/export/daily_activity?file_format=excel", headers=token_bat_cmdr)
        assert res.status_code == 200
        
        df = pd.read_excel(BytesIO(res.content))
        events = df['Event'].tolist()
        
        # Should see all 3 internal
        assert "Log_CoA" in events
        assert "Log_CoB" in events
        assert "Log_Bat1_HQ" in events
        
        # Should NOT see foreign
        assert "Log_Bat2_Foreign" not in events
        assert len(df) == 3

    def test_bat_tech_scope(self, client: TestClient, token_bat_tech, seed_operational_logs):
        """
        Battalion Tech: Same scope as Commander.
        """
        res = client.get("/reports/export/daily_activity?file_format=excel", headers=token_bat_tech)
        assert res.status_code == 200
        
        df = pd.read_excel(BytesIO(res.content))
        events = df['Event'].tolist()
        
        assert "Log_CoA" in events
        assert "Log_CoB" in events
        assert "Log_Bat1_HQ" in events
        assert "Log_Bat2_Foreign" not in events

    # ==========================================
    # Group C: Brigade Scope (All Seeing)
    # ==========================================

    def test_brigade_tech_scope(self, client: TestClient, token_brigade_tech, seed_operational_logs):
        """
        Brigade Tech:
        - MUST see EVERYTHING (Bat 1 + Bat 2 + Companies)
        """
        res = client.get("/reports/export/daily_activity?file_format=excel", headers=token_brigade_tech)
        assert res.status_code == 200
        
        df = pd.read_excel(BytesIO(res.content))
        events = df['Event'].tolist()
        
        # Should see all 4 events
        assert "Log_CoA" in events
        assert "Log_CoB" in events
        assert "Log_Bat1_HQ" in events
        assert "Log_Bat2_Foreign" in events
        assert len(df) == 4
