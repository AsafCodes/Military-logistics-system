"""
Out-of-band bootstrap for the initial MASTER account.

Deliberately NOT reachable over HTTP. Run once, on the host, with
BOOTSTRAP_ADMIN_ENABLED=1 set:

    BOOTSTRAP_ADMIN_ENABLED=1 python -m backend.bootstrap_admin <personal_number> "<full name>"

It creates the default profiles if they are missing, then the MASTER user.
The password is generated here and printed once. It is never read from
an argument or an environment variable, and it is never stored in plaintext.
"""
import os
import sys

from . import models, security
from .database import SessionLocal
from .migrations import run_migrations
from .profiles import BOOTSTRAP_PROFILES, PROFILES


def ensure_default_profiles(db) -> None:
    """Create any of the bootstrap profiles that are missing, by name."""
    existing = {name for (name,) in db.query(models.Profile.name)}
    created = [name for name in BOOTSTRAP_PROFILES if name not in existing]

    for name in created:
        db.add(models.Profile(name=name, **PROFILES[name]))

    if created:
        db.commit()
        print(f"Default profiles created ({', '.join(created)}).")


def bootstrap_admin(personal_number: str, full_name: str) -> None:
    if os.getenv("BOOTSTRAP_ADMIN_ENABLED") != "1":
        raise SystemExit(
            "Refusing to run: set BOOTSTRAP_ADMIN_ENABLED=1 to confirm this is an "
            "intentional, out-of-band bootstrap."
        )

    run_migrations()

    db = SessionLocal()
    try:
        if db.query(models.User).filter(models.User.role == models.UserRole.MASTER).first():
            raise SystemExit("Refusing to run: a MASTER account already exists.")
        if db.query(models.User).filter(models.User.personal_number == personal_number).first():
            raise SystemExit(f"Refusing to run: user {personal_number} already exists.")

        ensure_default_profiles(db)
        profile = db.query(models.Profile).filter(models.Profile.name == "Master").first()
        if profile is None:
            raise SystemExit(
                "Refusing to run: the Master profile is missing and could not be created. "
                "A MASTER without a profile fails every permission check in the system."
            )

        password = security.generate_password()
        admin = models.User(
            personal_number=personal_number,
            full_name=full_name,
            password_hash=security.get_password_hash(password),
            role=models.UserRole.MASTER,
            profile_id=profile.id,
            is_active_duty=True,
        )
        db.add(admin)
        db.commit()

        print("MASTER account created.")
        print(f"  personal_number: {personal_number}")
        print(f"  password:        {password}")
        print("Store it now — it is not recoverable and will not be shown again.")
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(
            'Usage: python -m backend.bootstrap_admin <personal_number> "<full name>"'
        )
    bootstrap_admin(sys.argv[1], sys.argv[2])
