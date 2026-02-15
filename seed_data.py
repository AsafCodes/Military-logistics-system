from sqlalchemy.orm import Session
from database import SessionLocal, engine
import models
from datetime import datetime
import security
import random

db = SessionLocal()

def seed_matrix():
    print("🌱 Seeding Green Table Matrix (Strict Mode)...")
    
    models.Base.metadata.drop_all(bind=engine)
    models.Base.metadata.create_all(bind=engine)
    
    # --- PROFILES (The Green Table) ---
    
    # 1. Master (מאסטר)
    p_master = models.Profile(
        name="Master",
        name_he="מאסטר",
        # Permissions
        can_generate_battalion_report=True, can_generate_company_report=True,
        can_view_battalion_realtime=True, can_view_company_realtime=True,
        can_change_assignment_others=True, can_change_maintenance_status=True,
        can_manage_locations=True, can_add_category=True, can_add_specific_item=True,
        can_remove_category=True, can_remove_specific_item=True,
        holds_equipment=True, must_report_presence=True,
        can_assign_roles=True # CRITICAL: Only Master has this
    )
    
    # 2. Brigade Tech Commander (מפקד צופן חטיבתי)
    p_brigade_tech_cmdr = models.Profile(
        name="Brigade Tech Commander",
        name_he="מפקד צופן חטיבתי",
        can_generate_battalion_report=True,
        can_generate_company_report=True,
        can_view_battalion_realtime=True,
        can_view_company_realtime=True, # Implicit if can view battalion? Let's be explicit based on request "can_view_company_realtime=True"
        can_change_assignment_others=True,
        can_change_maintenance_status=True,
        can_add_category=True,
        can_add_specific_item=True,
        can_remove_category=True,
        can_remove_specific_item=True,
        holds_equipment=True, must_report_presence=True,
        can_assign_roles=False
    )
    
    # 3. Brigade Tech Soldier (חייל צופן חטיבתי)
    p_brigade_tech_soldier = models.Profile(
        name="Brigade Tech Soldier",
        name_he="חייל צופן חטיבתי",
        can_change_maintenance_status=True,
        holds_equipment=True, must_report_presence=True,
        can_assign_roles=False
    )
    
    # 4. Battalion Tech Commander (מפקד טכני גדודי)
    p_bat_tech_cmdr = models.Profile(
        name="Battalion Tech Commander",
        name_he="מפקד טכני גדודי",
        can_generate_battalion_report=True,
        can_generate_company_report=True,
        can_view_battalion_realtime=True,
        can_change_assignment_others=True,
        can_change_maintenance_status=True,
        can_manage_locations=True,
        can_add_specific_item=True,
        can_remove_specific_item=True,
        holds_equipment=True, must_report_presence=True,
        can_assign_roles=False
    )
    
    # 5. Battalion Tech Soldier (חייל טכני גדודי)
    p_bat_tech_soldier = models.Profile(
        name="Battalion Tech Soldier",
        name_he="חייל טכני גדודי",
        can_change_maintenance_status=True,
        holds_equipment=True, must_report_presence=True,
        can_assign_roles=False
    )
    
    # 6. Company Commander (מפקד פלוגה)
    p_company_cmdr = models.Profile(
        name="Company Commander",
        name_he="מפקד פלוגה",
        can_generate_company_report=True,
        can_view_company_realtime=True,
        can_change_assignment_others=True, # Added to allow scoped transfers
        can_change_maintenance_status=True, # Added for flexibility
        holds_equipment=True, must_report_presence=True,
        can_assign_roles=False
        # Note: Scoped transfer logic in main.py restricts him to Co-level transfers
    )
    
    # 7. Company Tech Soldier (חייל טכני פלוגתי)
    p_company_tech = models.Profile(
        name="Company Tech Soldier",
        name_he="חייל טכני פלוגתי",
        can_view_company_realtime=True,
        can_change_maintenance_status=True,
        can_manage_locations=True,
        can_add_specific_item=True,
        holds_equipment=True, must_report_presence=True,
        can_assign_roles=False
    )
    
    # 8. Soldier (חייל פשוט)
    p_soldier = models.Profile(
        name="Soldier",
        name_he="חייל פשוט",
        holds_equipment=True, must_report_presence=True,
        can_assign_roles=False
    )
    
    # Add all profiles
    db.add_all([
        p_master, 
        p_brigade_tech_cmdr, p_brigade_tech_soldier,
        p_bat_tech_cmdr, p_bat_tech_soldier,
        p_company_cmdr, p_company_tech,
        p_soldier
    ])
    db.commit()
    
    # --- USERS ---
    pw = security.get_password_hash("secret")
    users = []

    # 1. Master
    u_master = models.User(personal_number="u_master", full_name="Master Admin", battalion="0", company="0", password_hash=pw, role=models.UserRole.MASTER, profile_id=p_master.id, unit_path="Global")
    
    # 2. Brigade Tech Commander
    u_brig_cmdr = models.User(personal_number="u_brig_cmdr", full_name="Cmdr Brigade Tech", battalion="HQ", company="HQ", password_hash=pw, role=models.UserRole.MANAGER, profile_id=p_brigade_tech_cmdr.id, unit_path="Brigade1")
    
    # 3. Brigade Tech Soldier
    u_brig_tech = models.User(personal_number="u_brig_tech", full_name="Tech Brigade", battalion="HQ", company="HQ", password_hash=pw, role=models.UserRole.TECHNICIAN, profile_id=p_brigade_tech_soldier.id, unit_path="Brigade1")
    
    # 4. Battalion Tech Commander (RENAMED & REASSIGNED)
    u_cmdr_bat_tech = models.User(personal_number="u_cmdr_bat_tech", full_name="Cmdr Tech Bat 1", battalion="1", company="HQ", password_hash=pw, role=models.UserRole.TECHNICIAN_MANAGER, profile_id=p_bat_tech_cmdr.id, unit_path="Brigade1/Bat1")
    
    # 5. Battalion Tech Soldier
    u_tech_bat = models.User(personal_number="u_tech_bat", full_name="Tech Bat 1", battalion="1", company="HQ", password_hash=pw, role=models.UserRole.TECHNICIAN, profile_id=p_bat_tech_soldier.id, unit_path="Brigade1/Bat1")

    # === Company A Users ===
    u_soldier_a = models.User(personal_number="u_soldier", full_name="Soldier Co A", battalion="1", company="A", password_hash=pw, role=models.UserRole.USER, profile_id=p_soldier.id, unit_path="Brigade1/Bat1/Co_A")
    u_tech_a = models.User(personal_number="u_tech_co", full_name="Tech Co A", battalion="1", company="A", password_hash=pw, role=models.UserRole.TECHNICIAN, profile_id=p_company_tech.id, unit_path="Brigade1/Bat1/Co_A")
    u_cmdr_a = models.User(personal_number="u_cmdr_co", full_name="Cmdr Co A", battalion="1", company="A", password_hash=pw, role=models.UserRole.MANAGER, profile_id=p_company_cmdr.id, unit_path="Brigade1/Bat1/Co_A")
    
    # === Company B Users ===
    u_soldier_b = models.User(personal_number="u_soldier_b", full_name="Soldier Co B", battalion="1", company="B", password_hash=pw, role=models.UserRole.USER, profile_id=p_soldier.id, unit_path="Brigade1/Bat1/Co_B")
    u_tech_b = models.User(personal_number="u_tech_b", full_name="Tech Co B", battalion="1", company="B", password_hash=pw, role=models.UserRole.TECHNICIAN, profile_id=p_company_tech.id, unit_path="Brigade1/Bat1/Co_B")
    u_cmdr_b = models.User(personal_number="u_cmdr_b", full_name="Cmdr Co B", battalion="1", company="B", password_hash=pw, role=models.UserRole.MANAGER, profile_id=p_company_cmdr.id, unit_path="Brigade1/Bat1/Co_B")

    # Aggregate
    users.extend([
        u_master, 
        u_brig_cmdr, u_brig_tech,
        u_cmdr_bat_tech, u_tech_bat,
        u_soldier_a, u_tech_a, u_cmdr_a,
        u_soldier_b, u_tech_b, u_cmdr_b
    ])
    
    db.add_all(users)
    db.commit()
    
    # --- CATALOGS & FAULTS ---
    catalog_names = ["Radio 710", "Radio 624", "Ceramic Vest", "Night Vision Goggle", "Tablet Mushad"]
    catalogs = []
    for name in catalog_names:
        c = models.CatalogItem(name=name)
        catalogs.append(c)
    db.add_all(catalogs)
    
    faults = ["Broken Screen", "No Signal", "Battery Dead", "Antenna Broken", "Software Glitch"]
    for f in faults:
        if not db.query(models.FaultType).filter_by(name=f).first():
            db.add(models.FaultType(name=f))
    db.commit()

    # --- MASSIVE EQUIPMENT SEEDING (20 Items) ---
    print("📦 Generating 20 equipment items...")
    
    # Refresh users to get IDs (Use A_Soldier and B_Soldier as default holders for items)
    user_map = {
        "A_Soldier": u_soldier_a.id, "A_Tech": u_tech_a.id,
        "B_Soldier": u_soldier_b.id, "B_Tech": u_tech_b.id
    }
    
    items = []
    
    for i in range(1, 21):
        # Even items -> Company A, Odd items -> Company B
        is_co_a = (i % 2 == 0)
        
        unit = "Brigade1/Bat1/Co_A" if is_co_a else "Brigade1/Bat1/Co_B"
        holder_id = user_map["A_Soldier"] if is_co_a else user_map["B_Soldier"]
        
        # Mix statuses
        status = "Functional"
        if i % 5 == 0: status = "Malfunctioning" # Every 5th item broken
        if i % 7 == 0: status = "Missing" # Every 7th item missing
        
        # Mix Catalog
        cat = catalogs[i % len(catalogs)]
        
        eq = models.Equipment(
            catalog_item_id=cat.id,
            status=status,
            unit_hierarchy=unit,
            holder_user_id=holder_id,
            owner_user_id=holder_id,
            sensitivity="UNCLASSIFIED",
            serial_number=str(random.randint(1000000, 9999999)), # Random 7-digit SN
            last_verified_at=datetime.utcnow()
        )
        items.append(eq)

    db.add_all(items)
    db.commit()
    print(f"🚀 Green Table Matrix Seeded Successfully! Created {len(items)} items across 2 companies.")

if __name__ == "__main__":
    seed_matrix()