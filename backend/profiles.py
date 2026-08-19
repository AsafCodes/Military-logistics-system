"""
Canonical profile definitions.

The permission matrix lives here, once. Both the seed script and the
out-of-band admin bootstrap build their Profile rows from this table, so a
new permission column cannot be set in one path and forgotten in the other.

Dependency-free by design: importing seed_data for these would open a
database session as an import side effect.
"""

PROFILES = {
    "Master": dict(
        name_he="מאסטר",
        can_generate_battalion_report=True, can_generate_company_report=True,
        can_view_all_equipment=True,
        can_view_battalion_realtime=True, can_view_company_realtime=True,
        can_change_assignment_others=True, can_change_maintenance_status=True,
        can_manage_locations=True, can_add_category=True, can_add_specific_item=True,
        can_remove_category=True, can_remove_specific_item=True,
        holds_equipment=True, must_report_presence=True,
        can_assign_roles=True,
    ),
    "Brigade Tech Commander": dict(
        name_he="מפקד צופן חטיבתי",
        can_generate_battalion_report=True, can_generate_company_report=True,
        can_view_battalion_realtime=True, can_view_company_realtime=True,
        can_change_assignment_others=True, can_change_maintenance_status=True,
        can_add_category=True, can_add_specific_item=True,
        can_remove_category=True, can_remove_specific_item=True,
        holds_equipment=True, must_report_presence=True,
        can_assign_roles=False,
    ),
    "Brigade Tech Soldier": dict(
        name_he="חייל צופן חטיבתי",
        can_change_maintenance_status=True,
        holds_equipment=True, must_report_presence=True,
        can_assign_roles=False,
    ),
    "Battalion Tech Commander": dict(
        name_he="מפקד טכני גדודי",
        can_generate_battalion_report=True, can_generate_company_report=True,
        can_view_battalion_realtime=True,
        can_change_assignment_others=True, can_change_maintenance_status=True,
        can_manage_locations=True,
        can_add_specific_item=True, can_remove_specific_item=True,
        holds_equipment=True, must_report_presence=True,
        can_assign_roles=False,
    ),
    "Battalion Tech Soldier": dict(
        name_he="חייל טכני גדודי",
        can_view_battalion_realtime=True,
        can_change_maintenance_status=True,
        holds_equipment=True, must_report_presence=True,
        can_assign_roles=False,
    ),
    "Company Commander": dict(
        name_he="מפקד פלוגה",
        can_generate_company_report=True,
        can_view_company_realtime=True,
        can_change_assignment_others=True, can_change_maintenance_status=True,
        holds_equipment=True, must_report_presence=True,
        can_assign_roles=False,
    ),
    "Company Tech Soldier": dict(
        name_he="חייל טכני פלוגתי",
        can_view_company_realtime=True,
        can_change_maintenance_status=True,
        can_manage_locations=True,
        can_add_specific_item=True,
        holds_equipment=True, must_report_presence=True,
        can_assign_roles=False,
    ),
    "Soldier": dict(
        name_he="חייל פשוט",
        holds_equipment=True, must_report_presence=True,
        can_assign_roles=False,
    ),
}

# The two a fresh system cannot function without: one privileged, one ordinary.
BOOTSTRAP_PROFILES = ("Master", "Soldier")
