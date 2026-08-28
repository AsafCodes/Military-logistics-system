"""
Shared enumerated types.

Kept dependency-free so both the ORM models and the Pydantic schemas can
import it without a cycle.
"""
import enum


class EquipmentStatus(str, enum.Enum):
    """The statuses an equipment record is meant to hold.

    Enforced on the verification write path only: the column is still a plain
    String, and routers/maintenance.py assigns literals directly. Analytics
    counts readiness by matching FUNCTIONAL exactly (routers/analytics.py),
    so free-form values on the unconstrained paths still corrupt the metric.

    These are the four options the verification form offers
    (frontend VerificationForm.tsx); keep the two in sync.
    """
    FUNCTIONAL = "Functional"
    MALFUNCTIONING = "Malfunctioning"
    IN_REPAIR = "In Repair"
    MISSING = "Missing"


class GroupKind(str, enum.Enum):
    """The kinds of group the access model recognises.

    Stored in groups.kind and used as the SQLAlchemy polymorphic identity
    (backend/authz.py). Nothing in the scoping algebra branches on this value:
    kinds exist so the org chart can say what a group *is*, not so scoping can
    treat one differently from another.
    """
    UNIT = "UNIT"
    TASK_FORCE = "TASK_FORCE"


class Capability(str, enum.Enum):
    """The verbs a grant can carry.

    Every member here names the route that consumes it. That is the rule, and
    it is what keeps this from becoming SEC-H4 a second time: six permissions
    were declared, seeded true and displayed to operators in the admin panel
    while no router consulted them, so the matrix asserted a denial the code
    never performed. The dangerous half of that was not the declaration -- it
    was seeding and showing it.

    H1-7 added TRANSFER one entry ahead of its gate and carried by no grant,
    on the argument that a capability nobody holds authorises nothing. H1-8
    closed that gap and added CREATE_EQUIPMENT in the same commit as the gate
    reading it. H1-9 gave REPORT_STATUS its routes and its grants and added
    RESOLVE_FAULT beside it, so no member waits any more: every verb here is
    read by a router, and the rule this docstring opens with describes the
    whole enum rather than most of it.

    RESOLVE_FAULT is the ONE placement in the entire cutover that is a
    judgement rather than a mapping, and it should be read with that in mind.
    Every other verb came from a column in profiles.py. This one could not:
    profiles.py has a single maintenance column, can_change_maintenance_status,
    so the table cannot say who may CLOSE a fault as distinct from who may
    REPORT one. It was split on the ruling that noticing a fault and declaring
    the item serviceable again are different authorities -- a company commander
    reports, the technical function closes. Company Commander is the only
    profile carrying that boolean which is not a tech, so it is the only
    profile the two verbs separate; if the split is wrong, that is the row it
    is wrong about. H1-10 is where profiles.py stops being the source of truth
    and where this judgement should be confirmed or reversed on purpose.

    MANAGE_CATALOG was declared absent here through H1-7, H1-8 and H1-9 on
    the argument that neither CatalogItem nor FaultType has a group, so the
    algebra had nothing to scope them by and the verb 'would not merely be
    unenforced, it would be unenforceable in this model'. The premise was
    right and the conclusion was wrong, and H1-10 corrects it rather than
    quietly deleting it.

    These rows really are global vocabulary and really have no group. The
    mistake was reading 'has no group' as 'has no place in the graph'. A
    resource belonging to the whole force is scoped by the node that MEANS
    the whole force, and the graph has one: the root. So MANAGE_CATALOG is
    required over every root (authz.require_global), which reads as authority
    over the entire graph rather than over any part of it -- there was never
    an expressiveness problem, only a missing call.

    One catalog verb rather than two, and the contrast with the pair above is
    deliberate. can_add_category and can_remove_category name the IDENTICAL
    set -- Master and Brigade Tech Commander. H1-9 split one column into two
    verbs because the split fell along a real difference; splitting these
    would produce two tables that differ only in name, which is the SEC-H4
    shape this docstring opens by warning about. Split them the moment some
    profile holds one without the other, and not before.
    """
    VIEW = "VIEW"
    # dependencies.scope_equipment_query, and every listing built on it.
    TRANSFER = "TRANSFER"
    # equipment.assign_owner, equipment.transfer_equipment.
    CREATE_EQUIPMENT = "CREATE_EQUIPMENT"
    # equipment.create_equipment, on the group the item will belong to.
    REPORT_STATUS = "REPORT_STATUS"
    # maintenance.report_fault, verifications.create_verification -- and, as a
    # question rather than a gate, report_fault's is_pending decision.
    RESOLVE_FAULT = "RESOLVE_FAULT"
    # maintenance.fix_equipment, on the group the item belongs to.
    MANAGE_CATALOG = "MANAGE_CATALOG"
    # setup.py's fault-type routes. Global vocabulary: held over every root.
    MANAGE_PERSONNEL = "MANAGE_PERSONNEL"
    # users.create_user, update_user_group, setup.list_groups. Also every root.


# SEC-H10. Which verbs answer to a flat yes/no (authz.require_global, asked
# over every root) and which are positional (authz.require, scoped by group)
# is a fact about each member above -- already stated as prose next to
# MANAGE_CATALOG and MANAGE_PERSONNEL. This is where it becomes something code
# can read, in the same file as that prose, so a new member's classification
# is decided once, here, rather than left implicit in which authz.py call a
# future router happens to use and cross-checked by hand against this file.
#
# _GLOBAL is the one place a member is named; both public tuples are pure
# derivations of it and of Capability's own declaration order, so nothing
# downstream can drift by editing only one of the two.
_GLOBAL = frozenset({Capability.MANAGE_PERSONNEL, Capability.MANAGE_CATALOG})
GLOBAL_CAPABILITIES = tuple(c for c in Capability if c in _GLOBAL)
SCOPED_CAPABILITIES = tuple(c for c in Capability if c not in _GLOBAL)
