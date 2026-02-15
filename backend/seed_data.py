"""
Seed Script for Military Logistics System
Works with backend package structure
"""

from sqlalchemy import text

from . import models, security
from .database import SessionLocal, engine

db = SessionLocal()


def seed_matrix():
    print("🌱 Seeding Green Table Matrix (Strict Mode)...")

    # CASCADE drop to handle tables with FKs not tracked by SQLAlchemy models
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.commit()
    models.Base.metadata.create_all(bind=engine)

    # --- PROFILES (The Green Table) ---

    # 1. Master (מאסטר)
    p_master = models.Profile(
        name="Master",
        name_he="מאסטר",
        can_generate_battalion_report=True,
        can_generate_company_report=True,
        can_view_battalion_realtime=True,
        can_view_company_realtime=True,
        can_change_assignment_others=True,
        can_change_maintenance_status=True,
        can_manage_locations=True,
        can_add_category=True,
        can_add_specific_item=True,
        can_remove_category=True,
        can_remove_specific_item=True,
        holds_equipment=True,
        must_report_presence=True,
        can_assign_roles=True,
    )

    # 2. Brigade Tech Commander
    p_brigade_tech_cmdr = models.Profile(
        name="Brigade Tech Commander",
        name_he="מפקד צופן חטיבתי",
        can_generate_battalion_report=True,
        can_generate_company_report=True,
        can_view_battalion_realtime=True,
        can_view_company_realtime=True,
        can_change_assignment_others=True,
        can_change_maintenance_status=True,
        can_add_category=True,
        can_add_specific_item=True,
        can_remove_category=True,
        can_remove_specific_item=True,
        holds_equipment=True,
        must_report_presence=True,
        can_assign_roles=False,
    )

    # 3. Brigade Tech Soldier
    p_brigade_tech_soldier = models.Profile(
        name="Brigade Tech Soldier",
        name_he="חייל צופן חטיבתי",
        can_change_maintenance_status=True,
        holds_equipment=True,
        must_report_presence=True,
        can_assign_roles=False,
    )

    # 4. Battalion Tech Commander
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
        holds_equipment=True,
        must_report_presence=True,
        can_assign_roles=False,
    )

    # 5. Battalion Tech Soldier
    p_bat_tech_soldier = models.Profile(
        name="Battalion Tech Soldier",
        name_he="חייל טכני גדודי",
        can_view_battalion_realtime=True,
        can_change_maintenance_status=True,
        holds_equipment=True,
        must_report_presence=True,
        can_assign_roles=False,
    )

    # 6. Company Commander
    p_company_cmdr = models.Profile(
        name="Company Commander",
        name_he="מפקד פלוגה",
        can_generate_company_report=True,
        can_view_company_realtime=True,
        can_change_assignment_others=True,
        can_change_maintenance_status=True,
        holds_equipment=True,
        must_report_presence=True,
        can_assign_roles=False,
    )

    # 7. Company Tech Soldier
    p_company_tech = models.Profile(
        name="Company Tech Soldier",
        name_he="חייל טכני פלוגתי",
        can_view_company_realtime=True,
        can_change_maintenance_status=True,
        can_manage_locations=True,
        can_add_specific_item=True,
        holds_equipment=True,
        must_report_presence=True,
        can_assign_roles=False,
    )

    # 8. Soldier
    p_soldier = models.Profile(
        name="Soldier",
        name_he="חייל פשוט",
        holds_equipment=True,
        must_report_presence=True,
        can_assign_roles=False,
    )

    db.add_all(
        [
            p_master,
            p_brigade_tech_cmdr,
            p_brigade_tech_soldier,
            p_bat_tech_cmdr,
            p_bat_tech_soldier,
            p_company_cmdr,
            p_company_tech,
            p_soldier,
        ]
    )
    db.commit()

    # --- USERS ---
    pw = security.get_password_hash("secret")
    users = []

    u_master = models.User(
        personal_number="u_master",
        full_name="Master Admin",
        password_hash=pw,
        role=models.UserRole.MASTER,
        profile_id=p_master.id,
        unit_hierarchy=None,
    )
    u_brig_cmdr = models.User(
        personal_number="u_brig_cmdr",
        full_name="Brigade Commander",
        password_hash=pw,
        role=models.UserRole.MANAGER,
        profile_id=p_brigade_tech_cmdr.id,
        unit_hierarchy="188",
    )
    u_bn_cmdr = models.User(
        personal_number="u_bn_cmdr",
        full_name="Battalion Commander",
        password_hash=pw,
        role=models.UserRole.MANAGER,
        profile_id=p_bat_tech_cmdr.id,
        unit_hierarchy="188/53",
    )
    u_tech_bat = models.User(
        personal_number="u_tech_bat",
        full_name="Bat Tech Soldier",
        password_hash=pw,
        role=models.UserRole.TECHNICIAN,
        profile_id=p_bat_tech_soldier.id,
        unit_hierarchy="188/53",
    )
    u_co_cmdr_a = models.User(
        personal_number="u_co_cmdr_a",
        full_name="Commander Co A",
        password_hash=pw,
        role=models.UserRole.MANAGER,
        profile_id=p_company_cmdr.id,
        unit_hierarchy="188/53/A",
    )
    u_co_cmdr_b = models.User(
        personal_number="u_co_cmdr_b",
        full_name="Commander Co B",
        password_hash=pw,
        role=models.UserRole.MANAGER,
        profile_id=p_company_cmdr.id,
        unit_hierarchy="188/53/B",
    )
    u_soldier = models.User(
        personal_number="u_soldier",
        full_name="Simple Soldier",
        password_hash=pw,
        role=models.UserRole.USER,
        profile_id=p_soldier.id,
        unit_hierarchy="188/53/A",
    )

    users.extend(
        [
            u_master,
            u_brig_cmdr,
            u_bn_cmdr,
            u_tech_bat,
            u_co_cmdr_a,
            u_co_cmdr_b,
            u_soldier,
        ]
    )
    db.add_all(users)
    db.commit()

    # --- CATALOGS & FAULTS ---
    catalog_names = [
        "Radio 710",
        "Radio 624",
        "Ceramic Vest",
        "Night Vision Goggle",
        "Tablet Mushad",
    ]
    catalogs = []
    for name in catalog_names:
        c = models.CatalogItem(name=name)
        catalogs.append(c)
    db.add_all(catalogs)

    faults = [
        "Broken Screen",
        "No Signal",
        "Battery Dead",
        "Antenna Broken",
        "Software Glitch",
    ]
    for f in faults:
        if not db.query(models.FaultType).filter_by(name=f).first():
            db.add(models.FaultType(name=f))
    db.commit()

    # --- EQUIPMENT SEEDING ---
    print("📦 Generating Equipment with Hierarchy...")

    items = []

    items.append(
        models.Equipment(
            catalog_item_id=catalogs[0].id,
            status="Functional",
            unit_hierarchy="188",
            holder_user_id=u_brig_cmdr.id,
            owner_user_id=u_brig_cmdr.id,
            serial_number="BRIG-001",
        )
    )

    items.append(
        models.Equipment(
            catalog_item_id=catalogs[1].id,
            status="Functional",
            unit_hierarchy="188/53",
            holder_user_id=u_bn_cmdr.id,
            owner_user_id=u_bn_cmdr.id,
            serial_number="BAT-001",
        )
    )

    for i in range(10):
        items.append(
            models.Equipment(
                catalog_item_id=catalogs[i % 5].id,
                status="Functional",
                unit_hierarchy="188/53/A",
                holder_user_id=u_co_cmdr_a.id,
                owner_user_id=u_co_cmdr_a.id,
                serial_number=f"CO-A-{i}",
            )
        )

    for i in range(10):
        items.append(
            models.Equipment(
                catalog_item_id=catalogs[i % 5].id,
                status="Functional",
                unit_hierarchy="188/53/B",
                holder_user_id=u_co_cmdr_b.id,
                owner_user_id=u_co_cmdr_b.id,
                serial_number=f"CO-B-{i}",
            )
        )

    items.append(
        models.Equipment(
            catalog_item_id=catalogs[2].id,
            status="Functional",
            unit_hierarchy="188/53/A",
            holder_user_id=u_soldier.id,
            owner_user_id=u_soldier.id,
            serial_number="9876543",
        )
    )

    db.add_all(items)
    db.commit()
    print("🚀 Hierarchy Seeded Successfully!")


if __name__ == "__main__":
    seed_matrix()
