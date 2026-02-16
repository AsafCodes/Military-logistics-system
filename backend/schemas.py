from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

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
    role: Optional[str] = None

# --- User ---
class ProfileResponse(BaseModel):
    name: str 
    can_view_company_realtime: bool = False
    can_view_battalion_realtime: bool = False # Added
    can_change_maintenance_status: bool = False
    can_change_assignment_others: bool = False
    can_add_category: bool = False

class UserBase(BaseModel):
    personal_number: str
    full_name: str
    role: Optional[str] = "user" 
    battalion: Optional[str] = None
    company: Optional[str] = None

class UserCreate(UserBase):
    password: str
    is_active_duty: bool = True

class UserResponse(UserBase):
    id: int
    is_active_duty: bool
    last_seen: datetime
    profile: Optional[ProfileResponse] = None

    class Config:
        from_attributes = True

class UserLogin(BaseModel):
    personal_number: str
    password: str

class PromoteUserRequest(BaseModel):
    target_user_id: int
    new_role: str

class UpdateProfileRequest(BaseModel):
    profile_id: int

# --- Equipment ---
class EquipmentCreate(BaseModel):
    catalog_name: str # e.g. "M4"
    serial_number: Optional[str] = None

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
    reported_status: str
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
