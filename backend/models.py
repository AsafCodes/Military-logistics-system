import enum
from datetime import datetime, timedelta

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from .database import Base  # Use shared Base from backend package


# --- Users & Authentication ---
class UserRole:
    MASTER = "master"
    MANAGER = "manager"
    TECHNICIAN_MANAGER = "technician_manager"
    TECHNICIAN = "technician"
    USER = "user"


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    personal_number = Column(String, unique=True, index=True)
    password_hash = Column(String)
    full_name = Column(String)
    role = Column(String, default="user")
    profile_id = Column(Integer, ForeignKey("profiles.id"), nullable=True)

    # Hierarchy Fields
    battalion = Column(String, nullable=True)  # e.g. "Gedud 51"
    company = Column(String, nullable=True)  # e.g. "Pluga B"
    unit_path = Column(
        String, nullable=True
    )  # e.g. "Golani/51/B" - Used for hierarchy permissions
    unit_hierarchy = Column(String, index=True, nullable=True)  # NEW: Materialized path

    # Status
    is_active_duty = Column(Boolean, default=True)  # Is currently in service?
    last_seen = Column(DateTime, default=datetime.utcnow)


class CatalogItem(Base):
    """
    Represents a type of equipment (e.g., 'M4 Carbine', 'Ceramic Vest Gen4').
    This avoids repeating 'M4 Carbine' strings in the equipment table.
    """

    __tablename__ = "catalog_items"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)  # e.g. "Mag"
    category = Column(String)  # "Weapon", "Optics", "Comms"
    description = Column(String, nullable=True)


class Location(Base):
    """
    Physical storage locations (Armory, Warehouse, Command Center).
    """

    __tablename__ = "locations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True)  # e.g. "Main Armory", "Pluga B Safe"
    type = Column(String)  # "Armory", "Container", "Room"

    # Hierarchy
    unit_path = Column(String, nullable=True)  # Who owns this location?

    @property
    def location_name(self):
        return self.name


class Equipment(Base):
    __tablename__ = "equipment"

    id = Column(Integer, primary_key=True, index=True)
    serial_number = Column(String, unique=True, nullable=True)

    catalog_item_id = Column(Integer, ForeignKey("catalog_items.id"), nullable=False)
    status = Column(String, default="Functional")

    # Matrix Security Fields
    sensitivity = Column(String, default="UNCLASSIFIED")
    unit_hierarchy = Column(String, index=True, nullable=True)  # NEW: Materialized path

    # --- Ownership vs Possession ---
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    owner_location_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    holder_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Custom Location String (e.g. "Armory", "Warehouse 1")
    custom_location = Column(String, nullable=True)

    actual_location_id = Column(Integer, ForeignKey("locations.id"), nullable=True)

    # Verification
    last_verified_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    catalog_item = relationship("CatalogItem")
    owner = relationship("User", foreign_keys=[owner_user_id])
    owner_location = relationship("Location", foreign_keys=[owner_location_id])
    holder = relationship("User", foreign_keys=[holder_user_id])
    location = relationship("Location", foreign_keys=[actual_location_id])

    # --- Smart Functions ---
    @property
    def item_name(self):
        return self.catalog_item.name if self.catalog_item else "Unknown"

    @property
    def current_state_description(self):
        location_desc = "לא ידוע"

        # 1. Physical Location
        if self.holder_user_id:
            holder_name = self.holder.full_name if self.holder else "Unknown"
            location_desc = f"אצל {holder_name}"
        elif self.custom_location:
            location_desc = f"ב-{self.custom_location}"
        elif self.actual_location_id:
            loc_name = self.location.location_name if self.location else "Unknown"
            location_desc = f"ב-{loc_name}"

        # 2. Ownership
        if self.owner_user_id:
            owner_name = self.owner.full_name if self.owner else "Unknown"

            if self.holder_user_id and self.holder_user_id != self.owner_user_id:
                holder_name = self.holder.full_name if self.holder else "Unknown"
                return f"שייך ל{owner_name}, אבל נמצא פיזית אצל {holder_name}"

            if self.custom_location:
                return f"שייך ל{owner_name}, נמצא ב{self.custom_location}"

            if self.actual_location_id:
                loc_name = self.location.location_name if self.location else "Unknown"
                return f"שייך ל{owner_name}, מאוחסן ב{loc_name}"

            return f"בשימוש שוטף אצל {owner_name}"

        return f"במלאי ללא בעלים (יתום), כרגע: {location_desc}"

    @property
    def compliance_level(self):
        if not self.last_verified_at:
            return "SEVERE"

        diff = datetime.utcnow() - self.last_verified_at
        if diff < timedelta(hours=24):
            return "GOOD"
        elif diff < timedelta(hours=48):
            return "WARNING"
        else:
            return "SEVERE"

    @property
    def report_status(self):
        if not self.last_verified_at:
            return "מעולם לא דווח"

        time_diff = datetime.utcnow() - self.last_verified_at
        if time_diff > timedelta(hours=24):
            return f"חריגת דיווח! עברו {time_diff.days} ימים ו-{int(time_diff.seconds / 3600)} שעות"
        return "דיווח תקין"


# --- Logs & History ---
class TransactionLog(Base):
    __tablename__ = "transaction_logs"
    id = Column(Integer, primary_key=True, index=True)
    equipment_id = Column(Integer, ForeignKey("equipment.id"))
    involved_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    involved_location_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    user_status_at_time = Column(Boolean, nullable=True)
    event_type = Column(String)

    # Faults
    is_returned_broken = Column(Boolean, default=False)
    broken_description = Column(String, nullable=True)

    # Location Traceability (CRITICAL)
    location = Column(String, nullable=True)

    equipment = relationship("Equipment")


# --- Maintenance ---
class FaultType(Base):
    __tablename__ = "fault_types"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True)
    severity = Column(Integer, default=1)

    # Manager Approval
    is_pending = Column(Boolean, default=False)
    requested_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)


class MaintenanceLog(Base):
    __tablename__ = "maintenance_logs"
    id = Column(Integer, primary_key=True, index=True)
    equipment_id = Column(Integer, ForeignKey("equipment.id"))
    fault_type_id = Column(Integer, ForeignKey("fault_types.id"))
    description = Column(String)
    status = Column(String, default="Open")
    opened_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)

    technician_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    equipment = relationship("Equipment")
    fault_type = relationship("FaultType")


# --- Security Profile (Matrix Model) ---
class Profile(BaseModel := Base):
    __tablename__ = "profiles"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(
        String, unique=True
    )  # e.g. "Soldier", "Company Commander", "Technician"
    name_he = Column(String, nullable=True)  # Hebrew Name

    # 1. View Permissions
    can_view_all_equipment = Column(Boolean, default=False)
    can_view_battalion_inventory = Column(
        Boolean, default=False
    )  # Maps to can_view_battalion_realtime
    can_view_battalion_realtime = Column(Boolean, default=False)  # Alias/Specific
    can_view_company_realtime = Column(Boolean, default=False)

    # 2. Action Permissions
    can_change_maintenance_status = Column(Boolean, default=False)  # Fix/Report
    can_mark_as_defective = Column(Boolean, default=False)

    # 3. Hierarchy/Transfer Permissions
    can_assign_equipment = Column(Boolean, default=False)  # Assign themselves
    can_change_assignment_others = Column(
        Boolean, default=False
    )  # Transfer from A to B
    can_assign_roles = Column(Boolean, default=False)  # Promote users

    # 4. Data Management
    can_add_category = Column(Boolean, default=False)  # Create new Catalog/Fault types
    can_add_specific_item = Column(Boolean, default=False)
    can_remove_category = Column(Boolean, default=False)
    can_remove_specific_item = Column(Boolean, default=False)
    can_manage_locations = Column(Boolean, default=False)

    # 5. Reports
    can_generate_battalion_report = Column(Boolean, default=False)
    can_generate_company_report = Column(Boolean, default=False)

    # 6. Logistics
    holds_equipment = Column(Boolean, default=True)
    must_report_presence = Column(Boolean, default=True)

    # Relations
    users = relationship("User", backref="profile")


# --- Solution Types ---
class SolutionType(Base):
    __tablename__ = "solution_types"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True)  # e.g. "Replace", "Fix"


# --- Analytics Cache ---
class DailyStats(Base):
    __tablename__ = "daily_stats"
    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, default=datetime.utcnow)
    total_items = Column(Integer)
    functional_items = Column(Integer)
    readiness_score = Column(Float)


# --- Verification & Status History ---
class Verification(Base):
    """Records equipment verification events."""

    __tablename__ = "verifications"

    id = Column(Integer, primary_key=True, index=True)
    equipment_id = Column(Integer, ForeignKey("equipment.id"), nullable=False)
    verification_type = Column(String, nullable=False)
    reported_status = Column(String, nullable=False)
    findings = Column(String, nullable=True)
    action_required = Column(Boolean, default=False)
    created_date = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)

    equipment = relationship("Equipment", backref="verifications")
    reporter = relationship("User", foreign_keys=[created_by])


class EquipmentStatusHistory(Base):
    """Audit trail for equipment status changes."""

    __tablename__ = "equipment_status_history"

    id = Column(Integer, primary_key=True, index=True)
    equipment_id = Column(Integer, ForeignKey("equipment.id"), nullable=False)
    old_status = Column(String, nullable=False)
    new_status = Column(String, nullable=False)
    change_reason = Column(String, nullable=False)
    verification_id = Column(Integer, ForeignKey("verifications.id"), nullable=True)
    notes = Column(String, nullable=True)
    created_date = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)

    equipment = relationship("Equipment", backref="status_history")
    verification = relationship("Verification", backref="status_changes")
    user = relationship("User", foreign_keys=[created_by])


# --- Ticket Status Enum ---


class TicketStatus(str, enum.Enum):
    OPEN = "Open"
    IN_PROGRESS = "In Progress"
    WAITING_PARTS = "Waiting for Parts"
    CLOSED = "Closed"
