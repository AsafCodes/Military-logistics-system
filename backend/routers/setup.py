"""Setup Router - System initialization and fault types"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from ..database import get_db
from ..dependencies import get_current_active_user, verify_admin_access
from .. import models
from .. import security

router = APIRouter(tags=["setup"])

@router.post("/setup/initialize_system")
def initialize_system(db: Session = Depends(get_db)):
    """Initialize system with default data (run once)"""
    if db.query(models.Profile).count() > 0:
        return {"status": "System already initialized"}
    
    # Create default profiles
    profiles = [
        models.Profile(name="Master", name_he="מאסטר", can_assign_roles=True, can_view_all_equipment=True),
        models.Profile(name="Soldier", name_he="חייל", holds_equipment=True, must_report_presence=True)
    ]
    db.add_all(profiles)
    db.commit()
    
    return {"status": "System initialized with default profiles"}

@router.get("/setup/fault_types")
def get_fault_types(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_active_user)):
    faults = db.query(models.FaultType).all()
    return [{"id": f.id, "name": f.name, "is_pending": f.is_pending} for f in faults]

@router.get("/setup/fault_types/pending")
def get_pending_fault_types(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_active_user)):
    """Get fault types that are pending manager approval."""
    if not (current_user.profile and current_user.profile.can_add_category):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    faults = db.query(models.FaultType).filter(models.FaultType.is_pending == True).all()
    return [{"id": f.id, "name": f.name, "is_pending": f.is_pending} for f in faults]

@router.post("/setup/fault_types")
def create_fault_type(
    name: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    if db.query(models.FaultType).filter(models.FaultType.name == name).first():
        raise HTTPException(status_code=400, detail="Fault type already exists")
    
    is_manager = current_user.profile and current_user.profile.can_add_category
    fault = models.FaultType(
        name=name,
        is_pending=not is_manager,
        requested_by_id=current_user.id
    )
    db.add(fault)
    db.commit()
    
    return {"status": "Created", "id": fault.id, "is_pending": fault.is_pending}

@router.put("/setup/fault_types/{fault_id}/approve")
def approve_fault_type(
    fault_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    if not (current_user.profile and current_user.profile.can_add_category):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    fault = db.query(models.FaultType).filter(models.FaultType.id == fault_id).first()
    if not fault:
        raise HTTPException(status_code=404, detail="Fault type not found")
    
    fault.is_pending = False
    db.commit()
    
    return {"status": "Approved", "id": fault.id}

@router.delete("/setup/fault_types/{fault_id}")
def delete_fault_type(
    fault_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    if not (current_user.profile and current_user.profile.can_remove_category):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    fault = db.query(models.FaultType).filter(models.FaultType.id == fault_id).first()
    if not fault:
        raise HTTPException(status_code=404, detail="Fault type not found")
    
    db.delete(fault)
    db.commit()
    
    return {"status": "Deleted"}
