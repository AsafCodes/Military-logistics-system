from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from datetime import datetime
from enum import Enum

# --- Enums (duplicated/imported for validation) ---
class UserRole(str, Enum):
    MASTER = "master"
    TECHNICIAN_MANAGER = "technician_manager"
    TECHNICIAN = "technician"
    MANAGER = "manager"
    USER = "user"

# --- User Schemas ---
class UserBase(BaseModel):
    personal_number: str
    full_name: str
    battalion: str
    company: str
    is_active_duty: bool = True
    role: UserRole = UserRole.USER

class UserCreate(UserBase):
    password: str

class ProfileResponse(BaseModel):
    name: str
    # Boolean Flags (The Green Table)
    can_generate_battalion_report: bool
    can_generate_company_report: bool
    can_view_battalion_realtime: bool
    can_view_company_realtime: bool
    can_change_assignment_others: bool
    can_change_maintenance_status: bool
    can_manage_locations: bool
    can_add_category: bool
    can_add_specific_item: bool
    can_remove_category: bool
    can_remove_specific_item: bool
    holds_equipment: bool
    must_report_presence: bool
    can_assign_roles: bool
    
    
    model_config = ConfigDict(from_attributes=True)

class ProfileSummary(BaseModel):
    id: int
    name: str
    model_config = ConfigDict(from_attributes=True)

class UserResponse(UserBase):
    id: int
    profile: Optional[ProfileResponse] = None # <--- This was missing!
    model_config = ConfigDict(from_attributes=True)

# --- Location Schemas ---
class LocationBase(BaseModel):
    location_name: str
    battalion: str
    company: str
    post: str

class LocationCreate(LocationBase):
    pass

class LocationResponse(LocationBase):
    id: int
    model_config = ConfigDict(from_attributes=True)

# --- Action Schemas (Requests) ---
class AssignOwnerRequest(BaseModel):
    equipment_id: int
    owner_id: int
    acting_user_id: int

class TransferPossessionRequest(BaseModel):
    equipment_id: int
    to_holder_id: int

class AssignLocationOwnerRequest(BaseModel):
    equipment_id: int
    location_id: int
    acting_user_id: int

class ReportPresenceRequest(BaseModel):
    equipment_id: int
    reporting_user_id: int

class ReportFaultRequest(BaseModel):
    equipment_id: int
    fault_name: str
    description: str

class CloseTicketRequest(BaseModel):
    ticket_id: int
    closing_user_id: int
    is_false_alarm: bool = False
    notes: Optional[str] = None

# --- Item/Equipment Schemas ---
class EquipmentCreate(BaseModel):
    catalog_name: str

class EquipmentResponse(BaseModel):
    id: int
    type: str
    status: str
    smart_description: str
    compliance_check: str
    compliance_level: str # New Field for UI Color
    serial_number: Optional[str] = None
    
    
    model_config = ConfigDict(from_attributes=True)

# --- Fault Type Schemas (NEW) ---
class FaultTypeCreate(BaseModel):
    name: str

class FaultTypeResponse(BaseModel):
    id: int
    name: str
    is_pending: bool
    model_config = ConfigDict(from_attributes=True)

# --- Missing Schemas Added Below ---

class TicketResponse(BaseModel):
    id: int
    equipment_id: int
    fault_type_id: Optional[int]
    status: str
    description: str
    created_at: datetime
    closed_at: Optional[datetime]
    is_false_alarm: bool
    tech_notes: Optional[str]
    
    timestamp: datetime 

    model_config = ConfigDict(from_attributes=True)

class PromoteUserRequest(BaseModel):
    target_user_id: int
    new_role: UserRole

class UnitReadinessResponse(BaseModel):
    total_items: int
    functional_items: int
    readiness_score: float

class UpdateProfileRequest(BaseModel):
    profile_id: int

# --- Auth Schemas ---
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None

# --- Report Schemas ---
class ReportItem(BaseModel):
    id: int
    type: str
    status: str
    location: str
    holder_name: Optional[str]
    holder_personal_number: Optional[str]
    last_verified_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)

class GeneralReportItem(BaseModel):
    item_type: str
    unit_association: Optional[str]
    designated_owner: Optional[str]
    actual_location: Optional[str]
    serial_number: str
    reporting_status: str
    last_reporter: str

    model_config = ConfigDict(from_attributes=True)

class ChangeItem(BaseModel):
    equipment_id: int
    name: str
    change_type: str
    details: str

class DailyActivityItem(BaseModel):
    item_type: str
    serial_number: str
    event_type: str          # e.g., "HANDOVER", "FAULT_REPORT"
    reporter_name: str       # Who performed the action
    timestamp: datetime      # This fixes "Invalid Date"
    details: Optional[str] = None
    status: str = "Unknown"  # Kept for frontend compatibility (color coding)

    model_config = ConfigDict(from_attributes=True)

class DailyMovementReportResponse(BaseModel):
    date: str
    items: List[DailyActivityItem]

# --- Admin / Permission Schemas ---



