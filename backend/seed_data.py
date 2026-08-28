"""
Seed Script for Military Logistics System
Works with backend package structure
"""
from sqlalchemy import text
from sqlalchemy.engine import make_url
from .database import DATABASE_URL, SessionLocal, engine
from . import authz
from . import models
from . import security
from .enums import Capability
from .migrations import run_migrations
import os
import sys

db = SessionLocal()

# Hosts that can only mean a developer machine. A file-based SQLite URL has no
# host at all, and "db" is the docker-compose service name.
LOCAL_HOSTS = {None, "", "localhost", "127.0.0.1", "::1", "db"}

# The seeded org chart: child -> parent, with None for the root.
#
# Declared, not derived by splitting a path string on "/". H1-11 dropped those
# strings; building the replacement model on top of the representation being
# deleted would have been the shortest route to deleting nothing.
#
# The names are path-shaped only because authz.Group.name is globally unique
# and "A" is not -- two battalions would each want a company called A. Nothing
# parses them as paths, and nothing should begin to.
#
# All four are Units. No TaskForce: the seed mirrors the formation that exists,
# and inventing a second parent to show off the DAG belongs in fixtures.
# tests/test_closure_engine.py already covers multi-parent containment.
GROUP_TREE = {
    "188": None,
    "188/53": "188",
    "188/53/A": "188/53",
    "188/53/B": "188/53",
}


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
    existing = db.query(models.User).count()
    if existing:
        raise SystemExit(
            f"Refusing to seed: the database already holds {existing} user "
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


def seed_group_graph():
    """Build the group DAG that the access model scopes on, and return it by name.

    Returned rather than re-queried so the equipment block cannot stamp a
    group_id that disagrees with the path it writes beside it.

    Edges go through authz.add_edge instead of raw GroupEdge rows: it is the
    public API, it refuses an edge that would close a cycle, and it leaves the
    closure agreeing with the edges when it returns. It rebuilds the closure
    once per call, which over four groups costs nothing.
    """
    groups = {name: authz.Unit(name=name) for name in GROUP_TREE}
    db.add_all(list(groups.values()))
    db.flush()  # add_edge needs the ids, and this session does not autoflush

    for name, parent in GROUP_TREE.items():
        if parent is not None:
            authz.add_edge(db, groups[parent].id, groups[name].id)

    db.commit()
    return groups


def seed_matrix(reset=False):
    require_seed_enabled()
    require_local_database()

    if reset:
        reset_schema()

    run_migrations()
    require_empty_database()

    print("🌱 Seeding Green Table Matrix (Strict Mode)...")

    # --- USERS ---
    # role and profile_id are gone (H1-12): where each of these sits is stated
    # once, below, as a GroupMembership row.
    u_master = models.User(personal_number="u_master", full_name="Master Admin")
    u_brig_cmdr = models.User(personal_number="u_brig_cmdr", full_name="Brigade Commander")
    u_bn_cmdr = models.User(personal_number="u_bn_cmdr", full_name="Battalion Commander")
    u_tech_bat = models.User(personal_number="u_tech_bat", full_name="Bat Tech Soldier")
    u_co_cmdr_a = models.User(personal_number="u_co_cmdr_a", full_name="Commander Co A")
    u_co_cmdr_b = models.User(personal_number="u_co_cmdr_b", full_name="Commander Co B")
    u_soldier = models.User(personal_number="u_soldier", full_name="Simple Soldier")

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

    # --- GROUP GRAPH (H1-4) ---
    print("🗺 Building the group graph...")
    groups = seed_group_graph()

    # Where each user SITS -- the only statement of it, since H1-11 dropped the
    # path strings that used to say the same thing beside it.
    #
    # Deliberately not folded together with view_grants below, which today
    # holds six of these same seven pairs. Being IN a group and having
    # authority OVER one are different facts that happen to coincide in a
    # seed this small; deriving one from the other would re-enact exactly the
    # conflation of place and permission this model exists to undo.
    #
    # u_master has no path at all and joins the root. None meant "unscoped"
    # only because the path encoding had no node standing for the whole force,
    # and the group model has one. H1-6 derives a new item's group from its
    # creator's membership, so a master with no membership would create
    # equipment belonging to nobody.
    memberships = [
        (u_master, "188"),
        (u_brig_cmdr, "188"),
        (u_bn_cmdr, "188/53"),
        (u_tech_bat, "188/53"),
        (u_co_cmdr_a, "188/53/A"),
        (u_co_cmdr_b, "188/53/B"),
        (u_soldier, "188/53/A"),
    ]

    # What each user may DO, and where. One table per verb, because a verb is
    # exactly what a grant carries and the three do not coincide -- reading the
    # table down a column is how you see that they do not.
    #
    # Placement is positional, so every ladder of booleans in profiles.py
    # reduces to which node a grant sits on:
    #
    #   the whole force -> the root
    #   a battalion     -> "188/53"
    #   a company       -> "188/53/A" or "188/53/B"
    #
    # u_brig_cmdr lands on the root because the visibility ladder resolved
    # their path "188" to the prefix "188", which already matched every seeded
    # item.
    #
    # VIEW reproduces exactly what dependencies.py resolved for these profiles
    # before H1-5, from can_view_all_equipment / can_view_battalion_realtime /
    # can_view_company_realtime.
    #
    # TRANSFER comes from can_change_assignment_others. It is VIEW's placement
    # minus u_tech_bat, whose Battalion Tech SOLDIER profile carries the view
    # flag and not the assignment one -- and that single exclusion is the whole
    # of what H1-8 restored. The old route granted transfer rights to any
    # profile whose NAME appeared in a hardcoded allowlist, and one of those
    # names had the permission column set false, so the allowlist overrode a
    # deliberate denial (SEC-H3). There is no allowlist to disagree with now.
    #
    # CREATE_EQUIPMENT comes from can_add_specific_item, which is already the
    # matrix's answer to "may add a specific item". Note the asymmetry it
    # carries over: Company Commander does NOT hold it and Company Tech Soldier
    # does. That is what profiles.py says today, and reproducing it faithfully
    # is the point -- if it is wrong it is wrong there, and H1-10 is where the
    # profile table stops being the source of truth.
    #
    # u_master's grants are a placement, not a bypass, and since H1-10 they are
    # the ONLY thing making that account privileged at all: is_master is deleted,
    # no router compares a role, and scope_equipment_query's short-circuit became
    # the VIEW row below. Delete these rows and the master sees nothing and can
    # do nothing -- which is the property test_master_acts_on_grants_and_not_on
    # _their_role asserts in both directions.
    #
    # It also means master is now BOUNDED BY THE GRAPH. The old bypass returned
    # every row unfiltered; a root grant returns desc(root), so a group in a
    # disconnected tree is invisible to them until someone grants it. That is a
    # narrowing, and a deliberate one.
    #
    # REPORT_STATUS comes from can_change_maintenance_status. That is every
    # profile except plain Soldier, who is covered for their own kit by the
    # possession arm in dependencies.require_status_authority and by nothing
    # else -- a grant over their company would let them write status onto every
    # item in it, which is wider than "report the rifle in your hands".
    #
    # RESOLVE_FAULT is the one table in this file that is a JUDGEMENT rather
    # than a mapping, and it should be read as one. profiles.py has a single
    # maintenance column, so it cannot distinguish reporting a fault from
    # closing one; the split says the technical function closes. In practice
    # that is REPORT_STATUS minus the company commanders, since Company
    # Commander is the only profile with the maintenance boolean that is not a
    # tech. If the two tables ever coincide again, the second verb has stopped
    # meaning anything -- that single exclusion is the whole of the difference.
    #
    # MANAGE_CATALOG and MANAGE_PERSONNEL are the two GLOBAL verbs, and their
    # placement on the root is not decorative: authz.require_global asks for
    # them over every root, so a grant anywhere below the top authorises
    # nothing at all. MANAGE_CATALOG comes from can_add_category and
    # can_remove_category, whose holders coincide; MANAGE_PERSONNEL comes from
    # can_assign_roles, which only the Master profile carries -- so it is the
    # narrowest table here and the one that replaced a role comparison.
    #
    # u_soldier gets NOTHING, deliberately. Their visibility is the own_only
    # holder filter, which is not a claim about groups -- VIEW over Co A would
    # take them from one item to eleven -- and they hold no authority to write.
    grants = {
        Capability.VIEW: [
            (u_master, "188"),
            (u_brig_cmdr, "188"),
            (u_bn_cmdr, "188/53"),
            (u_tech_bat, "188/53"),
            (u_co_cmdr_a, "188/53/A"),
            (u_co_cmdr_b, "188/53/B"),
        ],
        Capability.TRANSFER: [
            (u_master, "188"),
            (u_brig_cmdr, "188"),
            (u_bn_cmdr, "188/53"),
            (u_co_cmdr_a, "188/53/A"),
            (u_co_cmdr_b, "188/53/B"),
        ],
        Capability.CREATE_EQUIPMENT: [
            (u_master, "188"),
            (u_brig_cmdr, "188"),
            (u_bn_cmdr, "188/53"),
        ],
        Capability.REPORT_STATUS: [
            (u_master, "188"),
            (u_brig_cmdr, "188"),
            (u_bn_cmdr, "188/53"),
            (u_tech_bat, "188/53"),
            (u_co_cmdr_a, "188/53/A"),
            (u_co_cmdr_b, "188/53/B"),
        ],
        Capability.RESOLVE_FAULT: [
            (u_master, "188"),
            (u_brig_cmdr, "188"),
            (u_bn_cmdr, "188/53"),
            (u_tech_bat, "188/53"),
        ],
        Capability.MANAGE_CATALOG: [
            (u_master, "188"),
            (u_brig_cmdr, "188"),
        ],
        Capability.MANAGE_PERSONNEL: [
            (u_master, "188"),
        ],
    }

    # THE INVARIANT, stated once and enforced rather than hand-maintained.
    #
    #     a verb over equipment in a group implies VIEW of that group
    #
    # H1-9 and H1-10 each hand-placed a row to satisfy this and each labelled it
    # a judgement asking to be revisited: RESOLVE_FAULT's split from
    # REPORT_STATUS, and brigade_tech's root VIEW. Both were reaching for this
    # rule. Deriving the rows means brigade_tech's grant is no longer invented --
    # it is a consequence of holding maintenance verbs at the root -- and a verb
    # added to a table below can never again be authority nobody can exercise,
    # which is the defect H1-9 found the hard way.
    #
    # The GLOBAL verbs are excluded deliberately. MANAGE_CATALOG and
    # MANAGE_PERSONNEL sit on the root and authorise vocabulary and people, not
    # equipment; folding them in would hand anyone who can create a user sight of
    # every item in the force.
    #
    # The literal VIEW rows above are the independent ones -- the commanders'
    # visibility ladder, which exists whether or not they may act. Union, not
    # replacement: a VIEW grant is allowed to be broader than any single verb.
    for pair in sorted(
        authz.implied_view_placements(grants),
        key=lambda pair: (pair[0].personal_number, pair[1]),
    ):
        if pair not in grants[Capability.VIEW]:
            grants[Capability.VIEW].append(pair)

    db.add_all(
        [
            authz.GroupMembership(user_id=user.id, group_id=groups[name].id)
            for user, name in memberships
        ]
        + [
            authz.Grant(
                user_id=user.id,
                group_id=groups[name].id,
                capability=capability.value,
            )
            for capability, placements in grants.items()
            for user, name in placements
        ]
    )
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

    # --- EQUIPMENT SEEDING ---
    print("📦 Generating Equipment with Hierarchy...")

    def belongs_to(name):
        """Where an item belongs. One name, one column, since H1-11.

        This returned the path string beside group_id until H1-11 dropped the
        column, so that a group_id could not drift away from the path sitting
        next to it. There is now only the one representation to keep honest.
        """
        return {"group_id": groups[name].id}

    items = []

    items.append(models.Equipment(
        catalog_item_id=catalogs[0].id, status="Functional", **belongs_to("188"),
        holder_user_id=u_brig_cmdr.id, owner_user_id=u_brig_cmdr.id, serial_number="BRIG-001"
    ))

    items.append(models.Equipment(
        catalog_item_id=catalogs[1].id, status="Functional", **belongs_to("188/53"),
        holder_user_id=u_bn_cmdr.id, owner_user_id=u_bn_cmdr.id, serial_number="BAT-001"
    ))

    for i in range(10):
        items.append(models.Equipment(
            catalog_item_id=catalogs[i % 5].id, status="Functional", **belongs_to("188/53/A"),
            holder_user_id=u_co_cmdr_a.id, owner_user_id=u_co_cmdr_a.id, serial_number=f"CO-A-{i}"
        ))

    for i in range(10):
        items.append(models.Equipment(
            catalog_item_id=catalogs[i % 5].id, status="Functional", **belongs_to("188/53/B"),
            holder_user_id=u_co_cmdr_b.id, owner_user_id=u_co_cmdr_b.id, serial_number=f"CO-B-{i}"
        ))

    items.append(models.Equipment(
        catalog_item_id=catalogs[2].id, status="Functional", **belongs_to("188/53/A"),
        holder_user_id=u_soldier.id, owner_user_id=u_soldier.id, serial_number="9876543"
    ))

    db.add_all(items)
    db.commit()
    print(f"🚀 Hierarchy Seeded Successfully!")


if __name__ == "__main__":
    seed_matrix(reset="--reset" in sys.argv)
