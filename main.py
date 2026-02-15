from fastapi import FastAPI, Depends, HTTPException, status, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from database import SessionLocal, engine
from datetime import datetime, timedelta
from typing import List, Optional
from jose import JWTError, jwt

import models
import schemas
import security

# Initialize Database Tables
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Military Logistics System", version="4.0 - Secured")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ALLOW ALL ORIGINS
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# --- Dependencies ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, security.SECRET_KEY, algorithms=[security.ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = schemas.TokenData(username=username)
    except JWTError:
        raise credentials_exception
    
    user = db.query(models.User).filter(models.User.personal_number == token_data.username).first()
    if user is None:
        raise credentials_exception
    return user

async def get_current_active_user(current_user: models.User = Depends(get_current_user)):
    if not current_user.is_active_duty:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

# --- Helper Logic ---
def verify_admin_access(user: models.User):
    if user.role != models.UserRole.MASTER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Permission denied. Only MASTER can perform this action."
        )

# ==========================================
# 0. Auth & Base
# ==========================================
@app.post("/login", response_model=schemas.Token)
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.personal_number == form_data.username).first()
    if not user or not security.verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=security.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = security.create_access_token(
        data={"sub": user.personal_number}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/")
def read_root():
    return {"message": "Military Logistics System V4.0 (Secured) 🛡️"}

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)

@app.post("/setup/initialize_system")
def initialize_system(db: Session = Depends(get_db)):
    items = ["מכשיר קשר 710", "מכשיר קשר 624", "מכלול", "משקפת", "אפוד קרמי"]
    for i in items:
        if not db.query(models.CatalogItem).filter_by(name=i).first():
            db.add(models.CatalogItem(name=i))
    
    faults = ["לא נדלק", "מסך שבור", "אנטנה עקומה", "קורוזיה", "איבוד הצפנה", "נקרע"]
    for f in faults:
        if not db.query(models.FaultType).filter_by(name=f).first():
            db.add(models.FaultType(name=f))

    solutions = ["החלפה בחדש", "שליחה לדרג ד", "איפוס תוכנה", "הלחמה", "החלפת אנטנה"]
    for s in solutions:
        if not db.query(models.SolutionType).filter_by(name=s).first():
            db.add(models.SolutionType(name=s))
            
    db.commit()
    return {"status": "System Initialized & Catalogs Populated ✅"}

# ==========================================
# 1. User Management (Secured)
# ==========================================
@app.post("/users/", response_model=schemas.UserResponse)
def create_user(user: schemas.UserCreate, db: Session = Depends(get_db)):
    """
    Register a new user.
    - Password is hashed.
    - First user ever -> MASTER role, "Master" Profile.
    - All others -> USER role, "Soldier" Profile.
    """
    if db.query(models.User).filter(models.User.personal_number == user.personal_number).first():
        raise HTTPException(status_code=400, detail="User already exists")

    # Determine Role & Profile
    user_count = db.query(models.User).count()
    
    if user_count == 0:
        assigned_role = models.UserRole.MASTER
        target_profile_name = "Master"
    else:
        assigned_role = models.UserRole.USER
        target_profile_name = "Soldier"
        
    # Fetch Profile
    profile = db.query(models.Profile).filter(models.Profile.name == target_profile_name).first()
    
    # Handle missing profile (e.g. System not seeded)
    profile_id = profile.id if profile else None
    if not profile_id:
        # We allow creation but log/warn internally (or just proceed with None, as requested fallback)
        pass 

    hashed_pw = security.get_password_hash(user.password)
    
    new_user = models.User(
        personal_number=user.personal_number, 
        full_name=user.full_name, 
        battalion=user.battalion, 
        company=user.company, 
        password_hash=hashed_pw,
        is_active_duty=user.is_active_duty,
        role=assigned_role,
        profile_id=profile_id # Assign Profile
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@app.put("/users/promote", response_model=schemas.UserResponse)
def promote_user(
    req: schemas.PromoteUserRequest, 
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Promote a user.
    Requirement: Current user must be MASTER.
    """
    verify_admin_access(current_user)

    target_user = db.query(models.User).filter(models.User.id == req.target_user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="Target user not found")
    
    target_user.role = req.new_role
    db.commit()
    db.refresh(target_user)
    return target_user

@app.put("/users/{user_id}/profile", response_model=schemas.UserResponse)
def update_user_profile(
    user_id: int,
    req: schemas.UpdateProfileRequest,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Update a user's security profile.
    Requirement: MASTER only.
    """
    verify_admin_access(current_user)
    
    target_user = db.query(models.User).filter(models.User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
        
    profile = db.query(models.Profile).filter(models.Profile.id == req.profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
        
    target_user.profile_id = req.profile_id
    db.commit()
    db.refresh(target_user)
    return target_user

@app.get("/users/me/equipment", response_model=List[schemas.EquipmentResponse])
def get_my_equipment(
    current_user: models.User = Depends(get_current_active_user), 
    db: Session = Depends(get_db)
):
    """Get equipment held by the currently logged-in user."""
    items = db.query(models.Equipment).filter(models.Equipment.holder_user_id == current_user.id).all()
    
    # Manual mapping for property fields
    results = []
    for item in items:
        results.append(schemas.EquipmentResponse(
            id=item.id,
            type=item.item_name,
            status=item.status,
            smart_description=item.current_state_description,
            compliance_check=item.report_status,
            compliance_level=item.compliance_level
        ))
    return results

@app.get("/users/me", response_model=schemas.UserResponse)
def read_users_me(current_user: models.User = Depends(get_current_active_user)):
    """Get current user details with profile."""
    # Ensure profile is loaded if not already (though current_user usually has it from joinedload if session configured, or lazy)
    return current_user

@app.get("/equipment/accessible", response_model=List[schemas.EquipmentResponse])
def get_accessible_equipment(
    query_str: Optional[str] = None,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Get ALL equipment the user is allowed to see (Matrix Security).
    Uses security.get_visible_equipment().
    """
    # Base query
    q = db.query(models.Equipment)
    
    # Apply Security Filter
    q = security.get_visible_equipment(current_user, q)
    
    # Optional text filter (search by catalog name or status)
    if query_str:
        search = f"%{query_str}%"
        q = q.join(models.CatalogItem).filter(
            (models.CatalogItem.name.ilike(search)) |
            (models.Equipment.status.ilike(search))
        )
        
    items = q.all()
    
    results = []
    for item in items:
        results.append(schemas.EquipmentResponse(
            id=item.id,
            type=item.item_name,
            status=item.status,
            smart_description=item.current_state_description,
            compliance_check=item.report_status,
            compliance_level=item.compliance_level
        ))
    return results


# ==========================================
# 2. Equipment & Logistics
# ==========================================
@app.post("/equipment/", response_model=schemas.EquipmentResponse)
def create_equipment(
    item: schemas.EquipmentCreate, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    cat_item = db.query(models.CatalogItem).filter(models.CatalogItem.name == item.catalog_name).first()
    if not cat_item:
        raise HTTPException(status_code=400, detail="Unknown equipment type")
    
    new_item = models.Equipment(catalog_item_id=cat_item.id)
    db.add(new_item)
    db.commit()
    db.refresh(new_item)
    
    return schemas.EquipmentResponse(
        id=new_item.id,
        type=new_item.item_name,
        status=new_item.status,
        smart_description=new_item.current_state_description,
        compliance_check=new_item.report_status,
        compliance_level=new_item.compliance_level
    )

@app.post("/equipment/assign_owner/")
def assign_owner(
    req: schemas.AssignOwnerRequest, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    # Verify Profile Permission
    if not current_user.profile or not current_user.profile.can_change_assignment_others:
        raise HTTPException(status_code=403, detail="Permission denied. Profile cannot assign owners.")

    item = db.query(models.Equipment).filter(models.Equipment.id == req.equipment_id).first()
    if not item: raise HTTPException(status_code=404, detail="Item not found")
    
    item.owner_user_id = req.owner_id
    item.holder_user_id = req.owner_id
    item.actual_location_id = None
    item.last_verified_at = datetime.utcnow()
    
    db.commit()
    db.commit()
    return {"status": "Ownership Assigned", "state": item.current_state_description}

@app.post("/equipment/transfer")
def transfer_equipment(
    req: schemas.TransferPossessionRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """
    Transfer possession from one user to another.
    Rule: Equipment MUST be Functional.
    """
    # Permission Check
    if not current_user.profile or not current_user.profile.can_change_assignment_others:
         raise HTTPException(status_code=403, detail="Permission Denied: Cannot transfer equipment.")

    # 1. Verify target user exists
    target = db.query(models.User).filter(models.User.id == req.to_holder_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target user not found")

    # 2. Hierarchy Security Check (New Logic)
    # Company Commander -> Only within Company
    if current_user.profile.name == "Company Commander":
        if not current_user.unit_path or not target.unit_path or not target.unit_path.startswith(current_user.unit_path):
             raise HTTPException(status_code=403, detail="Permission Denied: Company Commander can only transfer within their company.")
    
    # Battalion Tech Commander -> Only within Battalion
    elif current_user.profile.name == "Battalion Tech Commander":
        if not current_user.unit_path or not target.unit_path or not target.unit_path.startswith(current_user.unit_path):
             raise HTTPException(status_code=403, detail="Permission Denied: Battalion Commander can only transfer within their battalion.")

    # 3. Get Equipment
    item = db.query(models.Equipment).filter(models.Equipment.id == req.equipment_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Equipment not found")

        
    old_holder_id = item.holder_user_id
    item.holder_user_id = target.id
    item.actual_location_id = None # Moved to a person, so clear location for now or logic depends
    item.last_verified_at = datetime.utcnow()
    
    # Optional: Log transaction
    log = models.TransactionLog(
        equipment_id=item.id,
        involved_user_id=current_user.id,
        event_type="HANDOVER",
        user_status_at_time=current_user.is_active_duty
    )
    db.add(log)
    
    db.commit()
    return {"status": "Transferred", "new_holder": target.full_name}

# ==========================================
# 3. Tickets & Maintenance
# ==========================================
@app.get("/tickets/", response_model=List[schemas.TicketResponse])
def get_all_tickets(
    status: Optional[str] = None, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    query = db.query(models.MaintenanceLog)
    if status:
        stat_enum = models.TicketStatus.OPEN if status.lower() == "open" else models.TicketStatus.CLOSED
        query = query.filter(models.MaintenanceLog.status == stat_enum)
            
    tickets = query.all()
    
    results = []
    for t in tickets:
        results.append(schemas.TicketResponse(
            id=t.id,
            equipment_id=t.equipment_id,
            fault_type_id=t.fault_type_id,
            status=t.status.value if hasattr(t.status, 'value') else t.status,
            description=t.description,
            created_at=t.timestamp,
            closed_at=t.closed_at,
            is_false_alarm=t.is_false_alarm,
            tech_notes=t.tech_notes
        ))
    return results

@app.get("/tickets/open", response_model=List[schemas.TicketResponse])
def get_open_tickets(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """Return all open tickets."""
    tickets = db.query(models.MaintenanceLog).filter(models.MaintenanceLog.status == models.TicketStatus.OPEN).all()
    results = []
    for t in tickets:
        results.append(schemas.TicketResponse(
            id=t.id,
            equipment_id=t.equipment_id,
            fault_type_id=t.fault_type_id,
            status=t.status.value if hasattr(t.status, 'value') else t.status,
            description=t.description,
            created_at=t.timestamp,
            closed_at=t.closed_at,
            is_false_alarm=t.is_false_alarm,
            tech_notes=t.tech_notes,
            timestamp=t.timestamp
        ))
    return results

@app.post("/maintenance/report")
def report_fault(
    req: schemas.ReportFaultRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    # 1. Find the equipment
    item = db.query(models.Equipment).filter(models.Equipment.id == req.equipment_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Equipment not found")
    
    # 2. Update status
    item.status = "Malfunctioning"
    
    # 3. Find or Create Fault Type (Simplified logic)
    fault_type = db.query(models.FaultType).filter(models.FaultType.name == req.fault_name).first()
    if not fault_type:
            # Fallback if specific fault not found
        fault_type = db.query(models.FaultType).first() 

    # 4. Create Maintenance Log (Ticket)
    new_ticket = models.MaintenanceLog(
        equipment_id=item.id,
        fault_type_id=fault_type.id,
        description=req.description,
        occurred_at_user_id=current_user.id,
        status=models.TicketStatus.OPEN,
        timestamp=datetime.utcnow()
    )
    
    db.add(new_ticket)

    # --- NEW: Write to Transaction Log (History) ---
    trans_log = models.TransactionLog(
        equipment_id=item.id,
        involved_user_id=current_user.id,
        event_type="FAULT_REPORT",
        user_status_at_time=current_user.is_active_duty,
        broken_description=f"{req.fault_name}: {req.description}",
        timestamp=datetime.utcnow()
    )
    db.add(trans_log)
    # -----------------------------------------------

    db.commit()
    return {"status": "Reported", "ticket_id": new_ticket.id}


# ==========================================
# 3. Setup & Management
# ==========================================
@app.get("/setup/fault_types", response_model=List[schemas.FaultTypeResponse])
def get_fault_types(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """Get all approved (non-pending) fault types."""
    return db.query(models.FaultType).filter(models.FaultType.is_pending == False).all()

@app.get("/setup/fault_types/pending", response_model=List[schemas.FaultTypeResponse])
def get_pending_fault_types(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """Get all pending fault types (Manager/Master only)."""
    # Use Profile Flag 'can_add_category' as proxy for Catalog Admin
    if not current_user.profile or not current_user.profile.can_add_category:
         raise HTTPException(status_code=403, detail="Not authorized")
    return db.query(models.FaultType).filter(models.FaultType.is_pending == True).all()

# --- Part 3 (User Req): New Endpoints ---

@app.get("/users", response_model=List[schemas.UserResponse])
def list_all_users(
    q: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    # Permission: Anyone? Or Restricted?
    # User Request: "backend supports listing users via GET /users/"
    # For Dropdown usage. Let's allow authenticated users for now.
    # We use joinedload to eagerly fetch the profile relationship to avoid N+1 and ensure data is there for Schema.
    from sqlalchemy.orm import joinedload
    query = db.query(models.User).options(joinedload(models.User.profile))
    
    if q:
        search = f"%{q}%"
        query = query.filter((models.User.full_name.ilike(search)) | (models.User.personal_number.ilike(search)))
    else:
        # Optimization: Limit result set if no search is provided
        query = query.limit(50)
        
    return query.all()

@app.post("/maintenance/fix/{equipment_id}")
def fix_equipment(
    equipment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    # Permission Check: can_change_maintenance_status
    if not current_user.profile or not current_user.profile.can_change_maintenance_status:
        raise HTTPException(status_code=403, detail="Permission Denied: Cannot change maintenance status.")
        
    item = db.query(models.Equipment).filter(models.Equipment.id == equipment_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
        
    # Check if item is accessible? 
    # Usually fixing implies physical access. 
    # But for now, just permission check.
    
    item.status = "Functional"
    
    # Close open tickets? (Optional, but good practice)
    open_tickets = db.query(models.MaintenanceLog).filter(
        models.MaintenanceLog.equipment_id == item.id,
        models.MaintenanceLog.status == models.TicketStatus.OPEN
    ).all()
    
    for t in open_tickets:
        t.status = models.TicketStatus.CLOSED
        t.closed_at = datetime.utcnow()
        t.closed_by_user_id = current_user.id
        
    # --- NEW: Write to Transaction Log (History) ---
    trans_log = models.TransactionLog(
        equipment_id=item.id,
        involved_user_id=current_user.id,
        event_type="REPAIR",
        user_status_at_time=current_user.is_active_duty,
        timestamp=datetime.utcnow()
    )
    db.add(trans_log)
    # -----------------------------------------------

    db.commit()
    return {"status": "Fixed", "item_id": item.id}

@app.post("/setup/fault_types", response_model=schemas.FaultTypeResponse)
def create_fault_type(
    req: schemas.FaultTypeCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """Create or request a new fault type."""
    # Check if exists
    existing = db.query(models.FaultType).filter(models.FaultType.name == req.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Fault type already exists")
    
    # Determine pending status based on role/profile
    is_authed_manager = current_user.profile and current_user.profile.can_add_category
    is_pending = not is_authed_manager
    
    new_ft = models.FaultType(
        name=req.name,
        is_pending=is_pending,
        requested_by_id=current_user.id
    )
    db.add(new_ft)
    db.commit()
    db.refresh(new_ft)
    return new_ft

@app.put("/setup/fault_types/{id}/approve", response_model=schemas.FaultTypeResponse)
def approve_fault_type(
    id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """Approve a pending fault type."""
    if not current_user.profile or not current_user.profile.can_add_category:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    ft = db.query(models.FaultType).filter(models.FaultType.id == id).first()
    if not ft:
        raise HTTPException(status_code=404, detail="Fault type not found")
    
    ft.is_pending = False
    db.commit()
    db.refresh(ft)
    return ft

@app.delete("/setup/fault_types/{id}")
def delete_fault_type(
    id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """Delete a fault type."""
    if not current_user.profile or not current_user.profile.can_add_category:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    ft = db.query(models.FaultType).filter(models.FaultType.id == id).first()
    if not ft:
        raise HTTPException(status_code=404, detail="Fault type not found")
    
    db.delete(ft)
    db.commit()
    return {"status": "deleted"}


# ==========================================
# 4. Analytics
# ==========================================
@app.get("/analytics/unit_readiness", response_model=schemas.UnitReadinessResponse)
def get_unit_readiness(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    total_items = db.query(models.Equipment).count()
    if total_items == 0:
        return schemas.UnitReadinessResponse(total_equipment=0, functional_count=0, readiness_score=0.0)
    
    functional_items = db.query(models.Equipment).filter(models.Equipment.status == "Functional").count()
    score = (functional_items / total_items) * 100.0
    
    return schemas.UnitReadinessResponse(
        total_items=total_items,
        functional_items=functional_items,
        readiness_score=round(score, 2)
    )

@app.get("/reports/query", response_model=List[schemas.GeneralReportItem])
def generate_inventory_report(
    equipment_type: Optional[str] = None,
    location_query: Optional[str] = None,
    status: Optional[str] = None,
    holder_name: Optional[str] = None,
    battalion_filter: Optional[str] = None,
    company_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """
    General Inventory Report V2.
    Supports filters: Battalion, Company (Hierarchy), Item Type, Holder.
    Returns: flattened GeneralReportItem list.
    """
    # 1. Base Query & Security
    query = db.query(models.Equipment)
    query = security.get_visible_equipment(current_user, query)
    
    # 2. Apply Filters
    if equipment_type:
        query = query.join(models.CatalogItem).filter(models.CatalogItem.name.ilike(f"%{equipment_type}%"))
        
    if status:
        query = query.filter(models.Equipment.status.ilike(f"%{status}%"))

    if location_query:
        # Search in Location names or Holder names
        search = f"%{location_query}%"
        query = query.outerjoin(models.Location).outerjoin(models.User, models.Equipment.holder_user_id == models.User.id).filter(
            (models.Location.location_name.ilike(search)) |
            (models.User.full_name.ilike(search))
        )
    
    if holder_name:
        search = f"%{holder_name}%"
        query = query.join(models.User, models.Equipment.holder_user_id == models.User.id).filter(
            (models.User.full_name.ilike(search)) |
            (models.User.personal_number.ilike(search))
        )
        
    if battalion_filter:
        # Filter by unit_hierarchy containing or starting with
        query = query.filter(models.Equipment.unit_hierarchy.ilike(f"%{battalion_filter}%"))
        
    if company_filter:
        # Strict hierarchy match or startswith
        query = query.filter(models.Equipment.unit_hierarchy.startswith(company_filter))

    items = query.all()
    results = []
    now = datetime.utcnow()
    
    for item in items:
        # Logic: Reporting Status
        reporting_status = "Missing"
        if item.last_verified_at:
             diff = now - item.last_verified_at
             if diff < timedelta(hours=24):
                 reporting_status = "Reported"
             elif diff < timedelta(hours=48):
                 reporting_status = "Late"

        # Logic: Last Reporter
        last_reporter = "Unknown"
        # 1. Try fetching from latest transaction
        last_tx = db.query(models.TransactionLog).filter(
            models.TransactionLog.equipment_id == item.id
        ).order_by(models.TransactionLog.timestamp.desc()).first()
        
        if last_tx and last_tx.involved_user_id:
             # Optimization: Could join, but doing lazy load for V1 simplicity
             # We assume involved_user is loaded if we queried relation? No, need to fetch.
             reporter = db.query(models.User).filter(models.User.id == last_tx.involved_user_id).first()
             if reporter:
                 last_reporter = reporter.full_name
        elif item.holder:
             last_reporter = item.holder.full_name # Fallback
        
        # Logic: Unit Association
        unit_assoc = item.unit_hierarchy
        if item.actual_location_id:
             unit_assoc = item.location.location_name
        
        # Logic: Actual Location
        actual_loc = "Unknown"
        if item.holder:
             actual_loc = item.holder.full_name
        elif item.actual_location_id:
             actual_loc = item.location.location_name
             
        results.append(schemas.GeneralReportItem(
            item_type=item.item_name,
            unit_association=unit_assoc if unit_assoc else "None",
            designated_owner=item.owner.full_name if item.owner else "None",
            actual_location=actual_loc,
            serial_number=item.serial_number or str(item.id), # Fallback to ID if SN missing
            reporting_status=reporting_status,
            last_reporter=last_reporter
        ))
        
    return results

@app.get("/reports/daily_movement", response_model=schemas.DailyMovementReportResponse)
def get_daily_movement_report(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """
    Returns the last 24h of operational events (Transactions + Alerts).
    """
    cutoff_time = datetime.utcnow() - timedelta(hours=24)
    report_items = []

    # Source A: Real Transactions (Handover, Faults, Fixes)
    logs = db.query(models.TransactionLog).filter(
        models.TransactionLog.timestamp >= cutoff_time
    ).all()

    for log in logs:
        # Fetch related objects safely
        item = db.query(models.Equipment).filter(models.Equipment.id == log.equipment_id).first()
        actor = db.query(models.User).filter(models.User.id == log.involved_user_id).first()
        
        if item:
            report_items.append(schemas.DailyActivityItem(
                item_type=item.item_name,
                serial_number=str(item.serial_number) if item.serial_number else str(item.id),
                event_type=log.event_type or "ACTION",
                reporter_name=actor.full_name if actor else "Unknown",
                timestamp=log.timestamp,
                details=log.broken_description or log.event_type
            ))

    # Source B: Missing Reports (Passive Alerts)
    missed_items = db.query(models.Equipment).filter(
        models.Equipment.last_verified_at < cutoff_time
    ).all()

    for item in missed_items:
        holder_name = item.holder.full_name if item.holder else "Unknown"
        report_items.append(schemas.DailyActivityItem(
            item_type=item.item_name,
            serial_number=str(item.serial_number) if item.serial_number else str(item.id),
            event_type="MISSING_REPORT",
            reporter_name="System Alert",
            timestamp=item.last_verified_at or datetime.utcnow(),
            details=f"Last seen with {holder_name}"
        ))

    # Sort descending (Newest first)
    report_items.sort(key=lambda x: x.timestamp, reverse=True)

    return schemas.DailyMovementReportResponse(
        date=datetime.utcnow().date().isoformat(),
        items=report_items
    )

# ==========================================
# 5. Admin Panel (Profiles)
# ==========================================
@app.get("/profiles", response_model=List[schemas.ProfileSummary])
def get_all_profiles(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """
    Get all available profiles for Admin assignment.
    Returns: List of {id, name}
    """
    verify_admin_access(current_user)
    return db.query(models.Profile).all()