from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

from .enums import EquipmentStatus

# --- Analytics ---
class UnitReadinessResponse(BaseModel):
    total_items: int
    functional_items: int
    readiness_percentage: float

# --- Token ---
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    personal_number: Optional[str] = None

# --- User ---
class GroupResponse(BaseModel):
    id: int
    name: str
    kind: str

    class Config:
        from_attributes = True

class UserBase(BaseModel):
    personal_number: str
    full_name: str

class UserCreate(UserBase):
    password: str
    is_active_duty: bool = True
    # Where the account sits, and required rather than derived: unlike
    # equipment creation, MANAGE_PERSONNEL is global (the personnel table
    # belongs to no unit), so there is no creator group to fall back to. H1-6
    # left create_user issuing no GroupMembership at all; this closes that gap
    # rather than carrying it forward again.
    group_id: int

class UserResponse(UserBase):
    id: int
    is_active_duty: bool
    last_seen: datetime
    group: Optional[GroupResponse] = None

    class Config:
        from_attributes = True

class UserLogin(BaseModel):
    personal_number: str
    password: str

class UpdateUserGroupRequest(BaseModel):
    group_id: int

class CapabilitiesResponse(BaseModel):
    """What the caller may do. SEC-H10 -- the frontend's first way to ask.

    Named `system`/`anywhere` rather than `global`/`scoped`: `global` is a
    Python keyword, and `anywhere` is chosen to say what it means at the
    point a consumer reads it, not just at the point it's declared.

    `system` is EXACT -- one may_global() call per entry, the same boolean
    the routes that gate on it already compute. Absence here means the
    server will refuse.

    `anywhere` is NOT a gate. Authority in this model is positional
    (authz.may): holding a verb over one group is not holding it over
    another. `anywhere` answers "does the caller hold this verb over ANY
    group" -- a superset of what they may act on. It over-shows: a consumer
    may offer a control for an item the caller cannot actually reach, and
    the backend will still refuse it. It never hides a control the caller
    is entitled to use. That asymmetry is why over-showing is the safe
    reading and under-showing would not be.
    """
    system: List[str]
    anywhere: List[str]

# --- Equipment ---
class EquipmentCreate(BaseModel):
    catalog_name: str # e.g. "M4"
    serial_number: Optional[str] = None

    # The group the item belongs to. Omitted, it is derived from the creator's
    # own membership -- the ordinary case, and the only one a client needs.
    # Supplied, it is an explicit override that the route validates against the
    # creator's extent, because unlike the derived value it is attacker-chosen.
    group_id: Optional[int] = None

class EquipmentResponse(BaseModel):
    id: int
    type: str # Computed from catalog
    serial_number: Optional[str]
    status: str
    
    holder_user_id: Optional[int]
    custom_location: Optional[str]
    actual_location_id: Optional[int]
    
    sensitivity: str = "UNCLASSIFIED"
    
    # Smart fields
    item_name: str
    current_state_description: str
    compliance_level: str
    report_status: str
    compliance_check: str 

    class Config:
        from_attributes = True

# --- Actions ---
class TransferPossessionRequest(BaseModel):
    equipment_id: int
    to_holder_id: Optional[int] = None
    to_location: Optional[str] = None # e.g. "Armory"

class AssignOwnerRequest(BaseModel):
    equipment_id: int
    owner_id: int

class ReportFaultRequest(BaseModel):
    equipment_id: int
    fault_name: str 
    description: str

class EquipmentVerifyRequest(BaseModel):
    equipment_id: int
    verification_code: Optional[str] = None 

# --- Setup ---
class FaultTypeCreate(BaseModel):
    name: str

class FaultTypeResponse(BaseModel):
    id: int
    name: str
    is_pending: bool

    class Config:
        from_attributes = True

class TicketResponse(BaseModel):
    id: int
    equipment_id: int
    fault_type_id: int
    
    equipment_name: str
    fault_type: str

    status: str
    description: str
    created_at: Optional[datetime] = None  # Alias for timestamp
    closed_at: Optional[datetime] = None
    
    is_false_alarm: bool = False
    tech_notes: Optional[str] = None
    
    timestamp: Optional[datetime] = None # DB field name

    class Config:
        from_attributes = True

class DailyActivityItem(BaseModel):
    timestamp: datetime
    event_type: str
    description: str
    reporter_name: str
    serial_number: str

    class Config:
        from_attributes = True

class InventoryReportItem(BaseModel):
    id: int
    item_type: str
    serial_number: Optional[str]
    unit_association: Optional[str]
    designated_owner: str
    actual_location: str
    reporting_status: str  # "Reported", "Late", "Missing"
    last_reporter: str
    last_verified_at: Optional[datetime]

    class Config:
        from_attributes = True


# --- Verification & Status History ---
class VerificationCreate(BaseModel):
    equipment_id: int
    verification_type: str
    reported_status: EquipmentStatus
    findings: Optional[str] = None
    action_required: bool = False


class VerificationResponse(BaseModel):
    id: int
    equipment_id: int
    verification_type: str
    reported_status: str
    findings: Optional[str]
    action_required: bool
    created_date: datetime
    created_by: int
    reporter_name: Optional[str] = None

    class Config:
        from_attributes = True


class StatusHistoryResponse(BaseModel):
    id: int
    equipment_id: int
    old_status: str
    new_status: str
    change_reason: str
    verification_id: Optional[int]
    notes: Optional[str]
    created_date: datetime
    created_by: int
    user_name: Optional[str] = None

    class Config:
        from_attributes = True
