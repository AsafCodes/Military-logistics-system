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


class Sensitivity(str, enum.Enum):
    """How restricted an equipment record is.

    DATA-H3-1. Two members, because two is what the domain has actually said:
    the ticket's own title is "classified items are always reported as
    UNCLASSIFIED", and nothing anywhere in this repository names a graded
    ladder. Inventing CONFIDENTIAL/SECRET/TOP_SECRET here would be declaring
    policy vocabulary no code, form or seed has asked for -- the shape the
    Capability docstring below opens by warning about. Extending is one line
    the moment something real needs the distinction.

    Constrains the RESPONSE, the REQUEST and the model default. The column is
    still a plain String (models.py), so an out-of-vocabulary value remains
    reachable by hand-written SQL -- DATA-H12 is the ticket that constrains the
    column, and until it lands such a value fails validation loudly rather than
    being silently reported as UNCLASSIFIED, which is the trade DATA-H3-1 makes
    on purpose. See tests/test_sensitivity_contract.py for that behaviour, and
    for the contrast with a bad value arriving on a REQUEST, which is refused
    with a 422 before the route runs at all.

    WRITABLE, as of DATA-H3-2: Capability.SET_SENSITIVITY gates both a
    dedicated PATCH route and the sensitivity clause of equipment creation, so
    CLASSIFIED is now reachable through the API by a named authority rather
    than by hand-written SQL alone.

    ENFORCED, as of DATA-H3-3, and this is the member's whole point: a
    CLASSIFIED item is hidden from a caller who lacks VIEW_CLASSIFIED over its
    group. The filter lives in dependencies.scope_equipment_query, so it reaches
    every listing, the maintenance and transaction-log listings built on
    scope_equipment_derived_query, and every 404 in the system through
    get_scoped_equipment_or_404 -- one definition rather than a rule each route
    reimplements. A CLASSIFIED value is no longer merely a claim recorded about
    the record; it is a restriction the server applies to it.

    Two deliberate limits on that guarantee, both load-bearing:

      - POSSESSION SURVIVES CLASSIFICATION. The holder arm of
        scope_equipment_query sits OUTSIDE the classification clause, so you can
        still see what you are carrying. Classification restricts everyone
        except the person physically holding the item -- which also keeps a
        private able to report a fault on the rifle in their hands.
      - AGGREGATES STILL LEAK. analytics.unit_readiness counts through the same
        scope, so an uncleared caller's total silently drops by one. Comparing
        totals with a cleared caller reveals that a classified item exists,
        though not which. Accepted rather than overlooked; see that route.
    """
    UNCLASSIFIED = "UNCLASSIFIED"
    CLASSIFIED = "CLASSIFIED"


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

    RESOLVE_FAULT was the ONE placement in the entire cutover that is a
    judgement rather than a mapping, and it should be read with that in mind.
    Every other verb of the cutover came from a column in profiles.py; the
    cutover is over, and SET_SENSITIVITY below is a second such judgement,
    added by DATA-H3-2 rather than migrated from anything. This one could not:
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

    SET_SENSITIVITY (DATA-H3-2) is the second judgement, and unlike
    RESOLVE_FAULT it did not come from splitting a column -- profiles.py has no
    classification boolean at all, so there was nothing to map. It is a verb of
    its own rather than a reuse of TRANSFER because reusing TRANSFER would
    assert that whoever may MOVE an item may CLASSIFY it, and TRANSFER is held
    by every company commander in both grant tables; that is the widest reading
    available for an authority that should be rarer than custody. Seeded to
    master, brigade and battalion and deliberately NOT to the company, on the
    ruling that classifying is a command decision above the level that carries
    the kit. If that ruling is wrong, the company rows are where it is wrong.

    Note it grants no possession arm anywhere. Holding an item lets you report
    on it (dependencies.require_status_authority) and does NOT let you classify
    it -- the same asymmetry RESOLVE_FAULT has, enforced the same way, by which
    routes call which helper rather than by anything inside this enum.

    VIEW_CLASSIFIED (DATA-H3-3) is the paired READ side of that verb, and the
    two are not the same kind of thing despite the adjacency. SET_SENSITIVITY is
    a verb you exercise ON an item; VIEW_CLASSIFIED is a widening of VIEW
    itself, which is why it is the one equipment-facing member deliberately
    kept OUT of authz.EQUIPMENT_CAPABILITIES -- the reasoning is stated in full
    beside that tuple. Seeded to the same three holders as SET_SENSITIVITY so
    that whoever may classify an item can still see it afterwards; splitting
    them would let a commander classify something into invisibility.
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
    SET_SENSITIVITY = "SET_SENSITIVITY"
    # equipment.set_sensitivity, and equipment.create_equipment when the
    # request names a sensitivity. On the group the item belongs to.
    VIEW_CLASSIFIED = "VIEW_CLASSIFIED"
    # dependencies.scope_equipment_query, as a widening of its VIEW arm rather
    # than a verb of its own. On the group the item belongs to.
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
