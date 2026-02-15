
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import Base, get_db
from backend import models
import backend.security as security
from datetime import datetime, timedelta
import random

from sqlalchemy.pool import StaticPool

# Use an in-memory SQLite database for testing
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="function")
def db_session():
    """Create a fresh database for each test."""
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="function")
def client(db_session):
    """Create a TestClient that uses the test database."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    
    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    del app.dependency_overrides[get_db]

@pytest.fixture(scope="function")
def mock_matrix_db(db_session):
    """
    Seed the database with the full hierarchy:
    - Master
    - Brigade 1: Brigade Tech Cmdr, Brigade Tech Soldier
    - Bat 1: Bat Tech Cmdr, Bat Tech Soldier
    - Co A: Co Cmdr, Tech Soldier, Soldier (with items)
    - Co B: Co Cmdr, Tech Soldier, Soldier (with items)
    """
    pw = security.get_password_hash("secret")
    
    # --- PROFILES ---
    profiles = {}
    
    # 1. Master
    profiles["Master"] = models.Profile(
        name="Master", name_he="מאסטר",
        can_generate_battalion_report=True, can_generate_company_report=True,
        can_view_battalion_realtime=True, can_view_company_realtime=True,
        can_change_assignment_others=True, can_change_maintenance_status=True,
        can_manage_locations=True, can_add_category=True, can_add_specific_item=True,
        can_remove_category=True, can_remove_specific_item=True,
        holds_equipment=True, must_report_presence=True, can_assign_roles=True
    )
    
    # 2. Brigade Commanders
    profiles["Brigade Tech Cmdr"] = models.Profile(
        name="Brigade Tech Commander", name_he="מפקד צופן חטיבתי",
        can_generate_battalion_report=True, can_generate_company_report=True,
        can_view_battalion_realtime=True, can_view_company_realtime=True,
        can_change_assignment_others=True, can_change_maintenance_status=True,
        can_add_category=True, can_add_specific_item=True,
        can_remove_category=True, can_remove_specific_item=True,
        holds_equipment=True, must_report_presence=True, can_assign_roles=False
    )
    
    profiles["Brigade Tech Soldier"] = models.Profile(
        name="Brigade Tech Soldier", name_he="חייל צופן חטיבתי",
        can_change_maintenance_status=True, 
        holds_equipment=True, must_report_presence=True, can_assign_roles=False
    )
    
    # 3. Battalion Commanders
    profiles["Battalion Tech Cmdr"] = models.Profile(
        name="Battalion Tech Commander", name_he="מפקד טכני גדודי",
        can_generate_battalion_report=True, can_generate_company_report=True,
        can_view_battalion_realtime=True,
        can_change_assignment_others=True, can_change_maintenance_status=True,
        can_manage_locations=True, can_add_specific_item=True, can_remove_specific_item=True,
        holds_equipment=True, must_report_presence=True, can_assign_roles=False
    )
    
    profiles["Battalion Tech Soldier"] = models.Profile(
        name="Battalion Tech Soldier", name_he="חייל טכני גדודי",
        can_change_maintenance_status=True,
        holds_equipment=True, must_report_presence=True, can_assign_roles=False
    )
    
    # 4. Company
    profiles["Company Commander"] = models.Profile(
        name="Company Commander", name_he="מפקד פלוגה",
        can_generate_company_report=True, can_view_company_realtime=True,
        can_change_assignment_others=True, can_change_maintenance_status=True,
        holds_equipment=True, must_report_presence=True, can_assign_roles=False
    )
    
    profiles["Company Tech Soldier"] = models.Profile(
        name="Company Tech Soldier", name_he="חייל טכני פלוגתי",
        can_view_company_realtime=False, can_change_maintenance_status=True,
        can_manage_locations=True, can_add_specific_item=True,
        holds_equipment=True, must_report_presence=True, can_assign_roles=False
    )
    
    profiles["Soldier"] = models.Profile(
        name="Soldier", name_he="חייל פשוט",
        holds_equipment=True, must_report_presence=True, can_assign_roles=False
    )
    
    for p in profiles.values():
        db_session.add(p)
    db_session.commit()
    
    # --- USERS ---
    users = {}
    
    # Master
    users["master"] = models.User(
        personal_number="u_master", full_name="Master Admin", 
        battalion="0", company="0", password_hash=pw, 
        role=models.UserRole.MASTER, profile_id=profiles["Master"].id, unit_path="Global",
        unit_hierarchy=None
    )
    
    # Brigade 1
    users["brigade_cmdr"] = models.User(
        personal_number="u_brig_cmdr", full_name="Cmdr Brigade Tech", 
        battalion="HQ", company="HQ", password_hash=pw, 
        role=models.UserRole.MANAGER, profile_id=profiles["Brigade Tech Cmdr"].id, unit_path="Brigade1",
        unit_hierarchy="188"
    )
    users["brigade_tech"] = models.User(
        personal_number="u_brig_tech", full_name="Tech Brigade", 
        battalion="HQ", company="HQ", password_hash=pw, 
        role=models.UserRole.TECHNICIAN, profile_id=profiles["Brigade Tech Soldier"].id, unit_path="Brigade1"
    )
    
    # Battalion 1
    users["bat_cmdr"] = models.User(
        personal_number="u_bat_cmdr", full_name="Cmdr Tech Bat 1", 
        battalion="1", company="HQ", password_hash=pw, 
        role=models.UserRole.TECHNICIAN_MANAGER, profile_id=profiles["Battalion Tech Cmdr"].id, unit_path="Brigade1/Bat1",
        unit_hierarchy="188/53"
    )
    users["bat_tech"] = models.User(
        personal_number="u_bat_tech", full_name="Tech Bat 1", 
        battalion="1", company="HQ", password_hash=pw, 
        role=models.UserRole.TECHNICIAN, profile_id=profiles["Battalion Tech Soldier"].id, unit_path="Brigade1/Bat1"
    )
    
    # Company A
    users["company_cmdr_a"] = models.User(
        personal_number="u_cmdr_a", full_name="Cmdr Co A", 
        battalion="1", company="A", password_hash=pw, 
        role=models.UserRole.MANAGER, profile_id=profiles["Company Commander"].id, unit_path="Brigade1/Bat1/Co_A",
        unit_hierarchy="188/53/A"
    )
    users["company_tech_a"] = models.User(
        personal_number="u_tech_a", full_name="Tech Co A", 
        battalion="1", company="A", password_hash=pw, 
        role=models.UserRole.TECHNICIAN, profile_id=profiles["Company Tech Soldier"].id, unit_path="Brigade1/Bat1/Co_A"
    )
    users["soldier_a"] = models.User(
        personal_number="u_soldier_a", full_name="Soldier Co A", 
        battalion="1", company="A", password_hash=pw, 
        role=models.UserRole.USER, profile_id=profiles["Soldier"].id, unit_path="Brigade1/Bat1/Co_A",
        unit_hierarchy=None # Soldier has no hierarchy view, only personal
    )
    
    # Company B
    users["company_cmdr_b"] = models.User(
        personal_number="u_cmdr_b", full_name="Cmdr Co B", 
        battalion="1", company="B", password_hash=pw, 
        role=models.UserRole.MANAGER, profile_id=profiles["Company Commander"].id, unit_path="Brigade1/Bat1/Co_B"
    )
    users["company_tech_b"] = models.User(
        personal_number="u_tech_b", full_name="Tech Co B", 
        battalion="1", company="B", password_hash=pw, 
        role=models.UserRole.TECHNICIAN, profile_id=profiles["Company Tech Soldier"].id, unit_path="Brigade1/Bat1/Co_B"
    )
    users["soldier_b"] = models.User(
        personal_number="u_soldier_b", full_name="Soldier Co B", 
        battalion="1", company="B", password_hash=pw, 
        role=models.UserRole.USER, profile_id=profiles["Soldier"].id, unit_path="Brigade1/Bat1/Co_B"
    )

    for u in users.values():
        db_session.add(u)
    db_session.commit()
    
    # --- CATALOG ---
    cat = models.CatalogItem(name="Standard Radio")
    db_session.add(cat)
    db_session.commit()
    
    # --- ITEMS ---
    # Give everyone a personal item, plus some unit items
    items = []
    
    # Soldier A Item
    items.append(models.Equipment(
        catalog_item_id=cat.id, status="Functional", unit_hierarchy="188/53/A",
        holder_user_id=users["soldier_a"].id, owner_user_id=users["soldier_a"].id,
        sensitivity="UNCLASSIFIED", serial_number="SA100", last_verified_at=datetime.utcnow()
    ))
    
    # Soldier B Item (Different Company)
    items.append(models.Equipment(
        catalog_item_id=cat.id, status="Functional", unit_hierarchy="Brigade1/Bat1/Co_B",
        holder_user_id=users["soldier_b"].id, owner_user_id=users["soldier_b"].id,
        sensitivity="UNCLASSIFIED", serial_number="SB200", last_verified_at=datetime.utcnow()
    ))
    
    # Tech A Item
    items.append(models.Equipment(
        catalog_item_id=cat.id, status="Functional", unit_hierarchy="Brigade1/Bat1/Co_A",
        holder_user_id=users["company_tech_a"].id, owner_user_id=users["company_tech_a"].id,
        sensitivity="UNCLASSIFIED", serial_number="TA300", last_verified_at=datetime.utcnow()
    ))
    
    for item in items:
        db_session.add(item)
    db_session.commit()

    return users

# --- AUTH FIXTURES ---

def create_auth_header(user_personal_number: str):
    access_token_expires = timedelta(minutes=30)
    access_token = security.create_access_token(
        data={"sub": user_personal_number}, expires_delta=access_token_expires
    )
    return {"Authorization": f"Bearer {access_token}"}

@pytest.fixture
def token_master(mock_matrix_db): return create_auth_header("u_master")

@pytest.fixture
def token_brigade_cmdr(mock_matrix_db): return create_auth_header("u_brig_cmdr")

@pytest.fixture
def token_brigade_tech(mock_matrix_db): return create_auth_header("u_brig_tech")

@pytest.fixture
def token_bat_cmdr(mock_matrix_db): return create_auth_header("u_bat_cmdr")

@pytest.fixture
def token_bat_tech(mock_matrix_db): return create_auth_header("u_bat_tech")

@pytest.fixture
def token_company_cmdr(mock_matrix_db): return create_auth_header("u_cmdr_a") # Co A Commander

@pytest.fixture
def token_company_tech(mock_matrix_db): return create_auth_header("u_tech_a") # Co A Tech

@pytest.fixture
def token_soldier(mock_matrix_db): return create_auth_header("u_soldier_a") # Co A Soldier

@pytest.fixture
def seed_operational_logs(mock_matrix_db, db_session):
    """
    Seed TransactionLogs to test Hierarchy Export boundaries.
    Creates 4 logs in different hierarchy levels.
    """
    # 1. Helper to find catalog item
    cat_id = db_session.query(models.CatalogItem).first().id
    
    # 2. Define Units and Events
    scenarios = [
        {"unit": "Brigade1/Bat1/Co_A", "event": "Log_CoA"},       # Company A
        {"unit": "Brigade1/Bat1/Co_B", "event": "Log_CoB"},       # Company B
        {"unit": "Brigade1/Bat1",      "event": "Log_Bat1_HQ"},   # Battalion HQ
        {"unit": "Brigade1/Bat2/Co_C", "event": "Log_Bat2_Foreign"} # Foreign Battalion
    ]
    
    # 3. Create Dummy Items & Logs
    for sc in scenarios:
        # Create Item
        item = models.Equipment(
            catalog_item_id=cat_id,
            status="Functional",
            unit_hierarchy=sc["unit"],
            serial_number=f"SN_{sc['event']}",
            last_verified_at=datetime.utcnow()
        )
        db_session.add(item)
        db_session.commit()
        
        # Create Log (Recent)
        log = models.TransactionLog(
            equipment_id=item.id,
            involved_user_id=1, # Doesn't matter for this test
            event_type=sc["event"], # Using event type as the traceable marker
            timestamp=datetime.utcnow(),
            broken_description="Test Log"
        )
        db_session.add(log)
    
    db_session.commit()
