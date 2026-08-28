from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Float
from sqlalchemy.orm import relationship
from datetime import datetime, timedelta
from .database import Base # Use shared Base from backend package
from .enums import EquipmentStatus

# Imported for its side effect: it registers the group algebra tables on
# Base.metadata, which is how alembic/env.py and the test suite's create_all()
# reach them. authz has no dependency on this module -- its foreign keys are
# declared by table-name string -- so the import direction is free. The
# 'groups.id' target below is resolved lazily, when DDL is emitted or a join is
# built, not at mapper configuration.
from . import authz  # noqa: F401

# --- Users & Authentication ---
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True, index=True)
    personal_number = Column(String, unique=True, index=True)
    password_hash = Column(String)
    full_name = Column(String)

    # Where a user SITS is a GroupMembership row, not a string on this table.
    # unit_path and unit_hierarchy were dropped in H1-11; role, profile_id,
    # battalion and company (the boolean permission matrix and the last two
    # hierarchy strings) are dropped here in H1-12, together with the Profile
    # and UserRole classes that gave them meaning. Nothing has read any of the
    # four for an authorization decision since H1-10 -- authority is grants
    # and group membership, and has been since H1-8.
    #
    # memberships (below) is the replacement for both role and the removed
    # battalion/company label: it says where a user sits, which is the thing
    # that actually decided what they could see even while the string columns
    # pretended otherwise.
    memberships = relationship("GroupMembership")

    # Status
    is_active_duty = Column(Boolean, default=True) # Is currently in service?
    last_seen = Column(DateTime, default=datetime.utcnow)

    @property
    def group(self):
        """The group this user sits in, or None if they belong to nowhere.

        Every write path this entry builds (creation, reassignment) maintains
        exactly one membership row per user, so "first" is safe here even
        though group_memberships is many-to-many in general -- see
        authz.primary_group_id's min() handling of genuinely incomparable
        memberships, a generality nothing here needs.
        """
        return self.memberships[0].group if self.memberships else None

class CatalogItem(Base):
    """
    Represents a type of equipment (e.g., 'M4 Carbine', 'Ceramic Vest Gen4').
    This avoids repeating 'M4 Carbine' strings in the equipment table.
    """
    __tablename__ = 'catalog_items'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False) # e.g. "Mag"
    category = Column(String) # "Weapon", "Optics", "Comms"
    description = Column(String, nullable=True)

class Location(Base):
    """
    Physical storage locations (Armory, Warehouse, Command Center).
    """
    __tablename__ = 'locations'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True) # e.g. "Main Armory", "Pluga B Safe"
    type = Column(String) # "Armory", "Container", "Room"

    @property
    def location_name(self):
        return self.name

class Equipment(Base):
    __tablename__ = 'equipment'
    
    id = Column(Integer, primary_key=True, index=True) 
    serial_number = Column(String, unique=True, nullable=True) 
    
    catalog_item_id = Column(Integer, ForeignKey('catalog_items.id'), nullable=False)
    status = Column(String, default=EquipmentStatus.FUNCTIONAL.value)
    
    # Matrix Security Fields
    sensitivity = Column(String, default="UNCLASSIFIED")

    # The group this item belongs to -- the single representation, since
    # H1-11 dropped the unit_hierarchy path string that used to sit above.
    #
    # NOT NULL as of H1-11. Every write path already refused to produce a NULL
    # -- creation fails closed when the creator has no group (H1-6), and both
    # move routes leave the group alone rather than clearing it -- so the
    # column was mandatory in fact and optional in the schema, which is the
    # kind of gap this phase exists to close. An item in no group is visible
    # to no commander, so there is no benign NULL to preserve.
    #
    # Deliberately no ondelete rule: deleting a group that still holds equipment
    # must fail rather than take the equipment with it. See the note in authz.py
    # on why that only actually holds on Postgres.
    #
    # The constraint is named explicitly so the model and the migration agree:
    # downgrade() drops it by name, which Postgres requires, and leaving it
    # unnamed here would have create_all() and Alembic build different schemas.
    group_id = Column(
        Integer,
        ForeignKey('groups.id', name='fk_equipment_group_id'),
        nullable=False,
        index=True,
    )
    
    # --- Ownership vs Possession ---
    owner_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    owner_location_id = Column(Integer, ForeignKey('locations.id'), nullable=True) 
    holder_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    
    # Custom Location String (e.g. "Armory", "Warehouse 1")
    custom_location = Column(String, nullable=True) 

    actual_location_id = Column(Integer, ForeignKey('locations.id'), nullable=True)

    # Verification
    last_verified_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    catalog_item = relationship("CatalogItem")
    owner = relationship("User", foreign_keys=[owner_user_id])
    owner_location = relationship("Location", foreign_keys=[owner_location_id])
    holder = relationship("User", foreign_keys=[holder_user_id])
    location = relationship("Location", foreign_keys=[actual_location_id])
    # Declared by class name, resolved lazily, like the groups.id FK above:
    # authz imports nothing from this module, so the direction stays one-way.
    group = relationship("Group")

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
            return f"חריגת דיווח! עברו {time_diff.days} ימים ו-{int(time_diff.seconds/3600)} שעות"
        return "דיווח תקין"

# --- Logs & History ---
class TransactionLog(Base):
    __tablename__ = 'transaction_logs'
    id = Column(Integer, primary_key=True, index=True)
    equipment_id = Column(Integer, ForeignKey('equipment.id'))
    involved_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    involved_location_id = Column(Integer, ForeignKey('locations.id'), nullable=True)
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
    __tablename__ = 'fault_types'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True) 
    severity = Column(Integer, default=1) 
    
    # Manager Approval
    is_pending = Column(Boolean, default=False)
    requested_by_id = Column(Integer, ForeignKey('users.id'), nullable=True)

class MaintenanceLog(Base):
    __tablename__ = 'maintenance_logs'
    id = Column(Integer, primary_key=True, index=True)
    equipment_id = Column(Integer, ForeignKey('equipment.id'))
    fault_type_id = Column(Integer, ForeignKey('fault_types.id'))
    description = Column(String)
    status = Column(String, default="Open") 
    opened_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)
    
    technician_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    
    equipment = relationship("Equipment")
    fault_type = relationship("FaultType")

# --- Solution Types ---
class SolutionType(Base):
    __tablename__ = 'solution_types'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True) # e.g. "Replace", "Fix"

# --- Analytics Cache ---
class DailyStats(Base):
    __tablename__ = 'daily_stats'
    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, default=datetime.utcnow)
    total_items = Column(Integer)
    functional_items = Column(Integer)
    readiness_score = Column(Float)


# --- Verification & Status History ---
class Verification(Base):
    """Records equipment verification events."""
    __tablename__ = 'verifications'
    
    id = Column(Integer, primary_key=True, index=True)
    equipment_id = Column(Integer, ForeignKey('equipment.id'), nullable=False)
    verification_type = Column(String, nullable=False)
    reported_status = Column(String, nullable=False)
    findings = Column(String, nullable=True)
    action_required = Column(Boolean, default=False)
    created_date = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    
    equipment = relationship("Equipment", backref="verifications")
    reporter = relationship("User", foreign_keys=[created_by])


class EquipmentStatusHistory(Base):
    """Audit trail for equipment status changes."""
    __tablename__ = 'equipment_status_history'
    
    id = Column(Integer, primary_key=True, index=True)
    equipment_id = Column(Integer, ForeignKey('equipment.id'), nullable=False)
    old_status = Column(String, nullable=False)
    new_status = Column(String, nullable=False)
    change_reason = Column(String, nullable=False)
    verification_id = Column(Integer, ForeignKey('verifications.id'), nullable=True)
    notes = Column(String, nullable=True)
    created_date = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    
    equipment = relationship("Equipment", backref="status_history")
    verification = relationship("Verification", backref="status_changes")
    user = relationship("User", foreign_keys=[created_by])


# --- Ticket Status Enum ---
import enum
class TicketStatus(str, enum.Enum):
    OPEN = "Open"
    IN_PROGRESS = "In Progress"
    WAITING_PARTS = "Waiting for Parts"
    CLOSED = "Closed"
