"""
Seed Script for Military Logistics System
Works with backend package structure
"""
from sqlalchemy import text
from sqlalchemy.engine import make_url
from .database import DATABASE_URL, SessionLocal, engine
from . import models
from . import security
from .migrations import run_migrations
from .profiles import PROFILES
import os
import sys

db = SessionLocal()

# Hosts that can only mean a developer machine. A file-based SQLite URL has no
# host at all, and "db" is the docker-compose service name.
LOCAL_HOSTS = {None, "", "localhost", "127.0.0.1", "::1", "db"}


def require_seed_enabled():
    """Refuse to seed unless the environment is explicitly flagged.

    Seeding creates accounts at every privilege level, which belongs nowhere
    but a local machine.
    """
    if os.getenv("SEED_ENABLED") != "1":
        raise SystemExit(
            "Refusing to seed: set SEED_ENABLED=1 to confirm this is a local "
            "environment. Seeding creates accounts at every privilege level."
        )


def require_local_database():
    """Refuse to touch a database that is not demonstrably local.

    SEED_ENABLED alone does not protect anything -- it travels in a shell
    profile or a .env, while DATABASE_URL is what decides which database gets
    dropped. This checks the target, not the intent.
    """
    host = make_url(DATABASE_URL).host
    if host not in LOCAL_HOSTS:
        raise SystemExit(
            f"Refusing to seed: DATABASE_URL points at host {host!r}, which is "
            f"not local. Expected one of {sorted(h for h in LOCAL_HOSTS if h)}."
        )


def require_empty_database():
    """Ordinary seeding only populates an empty database."""
    existing = db.query(models.Profile).count() + db.query(models.User).count()
    if existing:
        raise SystemExit(
            f"Refusing to seed: the database already holds {existing} profile/user "
            "rows. Seeding assumes empty tables. Re-run with --reset to DROP the "
            "schema first, which destroys all data."
        )


def reset_schema():
    """Drop and recreate the schema. Destroys all data."""
    print("💣 Resetting schema -- all data will be destroyed...")

    if engine.dialect.name != "postgresql":
        # SQLite is the documented local fallback and has no schemas at all.
        models.Base.metadata.drop_all(bind=engine)
        with engine.connect() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
            conn.commit()
        return

    # CASCADE drop to handle tables with FKs not tracked by SQLAlchemy models
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.commit()


def seed_matrix(reset=False):
    require_seed_enabled()
    require_local_database()

    if reset:
        reset_schema()

    run_migrations()
    require_empty_database()

    print("🌱 Seeding Green Table Matrix (Strict Mode)...")
    
    # --- PROFILES (The Green Table) ---
    # Definitions live in backend/profiles.py so the bootstrap path cannot drift.
    profiles = {name: models.Profile(name=name, **kwargs) for name, kwargs in PROFILES.items()}
    db.add_all(list(profiles.values()))
    db.commit()

    p_master = profiles['Master']
    p_brigade_tech_cmdr = profiles['Brigade Tech Commander']
    p_bat_tech_cmdr = profiles['Battalion Tech Commander']
    p_bat_tech_soldier = profiles['Battalion Tech Soldier']
    p_company_cmdr = profiles['Company Commander']
    p_soldier = profiles['Soldier']

    # --- USERS ---
    u_master = models.User(personal_number="u_master", full_name="Master Admin", role=models.UserRole.MASTER, profile_id=p_master.id, unit_hierarchy=None)
    u_brig_cmdr = models.User(personal_number="u_brig_cmdr", full_name="Brigade Commander", role=models.UserRole.MANAGER, profile_id=p_brigade_tech_cmdr.id, unit_hierarchy="188")
    u_bn_cmdr = models.User(personal_number="u_bn_cmdr", full_name="Battalion Commander", role=models.UserRole.MANAGER, profile_id=p_bat_tech_cmdr.id, unit_hierarchy="188/53")
    u_tech_bat = models.User(personal_number="u_tech_bat", full_name="Bat Tech Soldier", role=models.UserRole.TECHNICIAN, profile_id=p_bat_tech_soldier.id, unit_hierarchy="188/53")
    u_co_cmdr_a = models.User(personal_number="u_co_cmdr_a", full_name="Commander Co A", role=models.UserRole.MANAGER, profile_id=p_company_cmdr.id, unit_hierarchy="188/53/A")
    u_co_cmdr_b = models.User(personal_number="u_co_cmdr_b", full_name="Commander Co B", role=models.UserRole.MANAGER, profile_id=p_company_cmdr.id, unit_hierarchy="188/53/B")
    u_soldier = models.User(personal_number="u_soldier", full_name="Simple Soldier", role=models.UserRole.USER, profile_id=p_soldier.id, unit_hierarchy="188/53/A")

    users = [u_master, u_brig_cmdr, u_bn_cmdr, u_tech_bat, u_co_cmdr_a, u_co_cmdr_b, u_soldier]

    credentials = []
    for user in users:
        password = security.generate_password()
        user.password_hash = security.get_password_hash(password)
        credentials.append((user.personal_number, password))

    db.add_all(users)
    db.commit()

    print("")
    print("Seeded account passwords -- shown once, not recoverable:")
    for personal_number, password in credentials:
        print(f"  {personal_number:<14} {password}")
    
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

    # --- EQUIPMENT SEEDING ---
    print("📦 Generating Equipment with Hierarchy...")
    
    items = []
    
    items.append(models.Equipment(
        catalog_item_id=catalogs[0].id, status="Functional", unit_hierarchy="188", 
        holder_user_id=u_brig_cmdr.id, owner_user_id=u_brig_cmdr.id, serial_number="BRIG-001"
    ))

    items.append(models.Equipment(
        catalog_item_id=catalogs[1].id, status="Functional", unit_hierarchy="188/53", 
        holder_user_id=u_bn_cmdr.id, owner_user_id=u_bn_cmdr.id, serial_number="BAT-001"
    ))

    for i in range(10):
        items.append(models.Equipment(
            catalog_item_id=catalogs[i % 5].id, status="Functional", unit_hierarchy="188/53/A",
            holder_user_id=u_co_cmdr_a.id, owner_user_id=u_co_cmdr_a.id, serial_number=f"CO-A-{i}"
        ))

    for i in range(10):
        items.append(models.Equipment(
            catalog_item_id=catalogs[i % 5].id, status="Functional", unit_hierarchy="188/53/B",
            holder_user_id=u_co_cmdr_b.id, owner_user_id=u_co_cmdr_b.id, serial_number=f"CO-B-{i}"
        ))

    items.append(models.Equipment(
        catalog_item_id=catalogs[2].id, status="Functional", unit_hierarchy="188/53/A",
        holder_user_id=u_soldier.id, owner_user_id=u_soldier.id, serial_number="9876543"
    ))

    db.add_all(items)
    db.commit()
    print(f"🚀 Hierarchy Seeded Successfully!")


if __name__ == "__main__":
    seed_matrix(reset="--reset" in sys.argv)
