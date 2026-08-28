"""
Out-of-band bootstrap for the initial MASTER account.

Deliberately NOT reachable over HTTP. Run once, on the host, with
BOOTSTRAP_ADMIN_ENABLED=1 set:

    BOOTSTRAP_ADMIN_ENABLED=1 python -m backend.bootstrap_admin <personal_number> "<full name>"

It creates the MASTER user and grants it root authority. The password is
generated here and printed once. It is never read from an argument or an
environment variable, and it is never stored in plaintext.
"""
import os
import sys

from . import authz, models, security
from .database import SessionLocal
from .enums import Capability
from .migrations import run_migrations

# The group a bootstrapped MASTER commands when the database has no org chart
# at all. Named rather than derived: there is nothing to derive it from, and a
# real deployment renames it or builds its own tree above it.
ROOT_GROUP_NAME = "ROOT"

# Every capability the bootstrapped MASTER receives over each root, named one
# by one on purpose. This used to be `for capability in Capability`, which
# meant any verb added to the enum was conferred here silently -- the enum was
# load-bearing in a file nobody opens when adding a verb, and H1-9 and H1-10
# each added two without anyone deciding they belonged to this account.
#
# The list happens to be the whole enum today, and test_bootstrap_authority
# asserts exactly that. The point is not that it differs -- it is that adding
# a verb now FAILS a test and forces the decision to be made out loud, in
# either direction.
ROOT_CAPABILITIES = (
    Capability.VIEW,
    Capability.TRANSFER,
    Capability.CREATE_EQUIPMENT,
    Capability.REPORT_STATUS,
    Capability.RESOLVE_FAULT,
    Capability.MANAGE_CATALOG,
    Capability.MANAGE_PERSONNEL,
)


def already_bootstrapped(db) -> bool:
    """Whether a MASTER has already been granted root authority.

    Replaces a `role == UserRole.MASTER` comparison (H1-12 drops role
    entirely). grant_root_authority always issues a GroupMembership on every
    root for the account it bootstraps, so checking for that membership finds
    the same fact the role comparison used to approximate, without a column
    dedicated to answering it.
    """
    root_ids = [group.id for group in authz.root_groups(db)]
    if not root_ids:
        return False
    return (
        db.query(authz.GroupMembership)
        .filter(authz.GroupMembership.group_id.in_(root_ids))
        .first()
        is not None
    )


def grant_root_authority(db, user: models.User) -> list[str]:
    """Place the new MASTER at the top of the graph and grant it everything there.

    Not optional, and not a convenience. Before H1-8 the equipment routes
    carried `or current_user.role == "master"`, so a MASTER with no group, no
    membership and no grant -- exactly what this script used to produce -- still
    worked. That comparison is gone: authority is grants and nothing else now,
    so without this the one supported way to create the first administrator
    would produce an account that can neither create equipment nor transfer it.

    Every capability, on every ROOT. A root is a group nothing contains, which
    is the only definition available here -- this script cannot know an
    organisation it is being run to bootstrap. Granting on each root rather than
    on one guessed group is what makes this correct for an operator who built
    their tree by hand before running it; authority is positional, so a grant on
    a root reaches that root's entire subtree and nothing else.

    Membership is issued alongside, and is a different fact: the grant says what
    this account commands, the membership says where it stands. H1-6 derives a
    new item's group from its creator's membership, so a master without one
    creates equipment belonging to nobody.

    rebuild_closure is not optional when a group is created here. A Group added
    on its own has no closure row at all -- not even the depth-0 self-row that
    desc(G) is carried by -- so it would be reachable by no grant, including the
    direct one issued below, and this function would appear to succeed while
    granting nothing.
    """
    # authz.root_groups rather than the query this used to inline. H1-10 gave
    # require_global a second need for the same predicate, and one definition
    # of "the top" is the whole point of keeping it there.
    roots = authz.root_groups(db)

    if not roots:
        root = authz.Unit(name=ROOT_GROUP_NAME)
        db.add(root)
        db.flush()
        authz.rebuild_closure(db)
        roots = [root]

    for group in roots:
        db.add(authz.GroupMembership(user_id=user.id, group_id=group.id))
        for capability in ROOT_CAPABILITIES:
            db.add(
                authz.Grant(
                    user_id=user.id, group_id=group.id, capability=capability.value
                )
            )
    db.commit()
    return [group.name for group in roots]


def bootstrap_admin(personal_number: str, full_name: str) -> None:
    if os.getenv("BOOTSTRAP_ADMIN_ENABLED") != "1":
        raise SystemExit(
            "Refusing to run: set BOOTSTRAP_ADMIN_ENABLED=1 to confirm this is an "
            "intentional, out-of-band bootstrap."
        )

    run_migrations()

    db = SessionLocal()
    try:
        if already_bootstrapped(db):
            raise SystemExit(
                "A MASTER already holds root authority; no new one created."
            )
        if db.query(models.User).filter(models.User.personal_number == personal_number).first():
            raise SystemExit(f"Refusing to run: user {personal_number} already exists.")

        password = security.generate_password()
        admin = models.User(
            personal_number=personal_number,
            full_name=full_name,
            password_hash=security.get_password_hash(password),
            is_active_duty=True,
        )
        db.add(admin)
        db.commit()

        roots = grant_root_authority(db, admin)

        print("MASTER account created.")
        print(f"  commands:        {', '.join(roots)} (every capability)")
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
