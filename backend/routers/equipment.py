"""
Equipment Router - Equipment CRUD and transfer endpoints
CRITICAL: Contains Hierarchical Data Scoping logic
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from datetime import datetime
from typing import List, Optional

from ..database import get_db
from ..dependencies import get_current_active_user, get_daily_status
from .. import models
from .. import schemas

router = APIRouter(tags=["equipment"])

@router.get("/equipment/accessible", response_model=List[schemas.EquipmentResponse])
def get_accessible_equipment(
    query_str: Optional[str] = None,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Get ALL equipment the user is allowed to see (Matrix Security).
    CRITICAL: Hierarchical Data Scoping Logic
    """
    q = db.query(models.Equipment)
    
    # 1. Apply Security Filter (Hierarchical Scoping)
    # Use unit_hierarchy for matching (e.g., "188/53" matches equipment with "188/53/A")
    user_hierarchy = current_user.unit_hierarchy or current_user.unit_path
    
    # MASTER role always sees everything
    if current_user.role == models.UserRole.MASTER or current_user.role == "master":
        pass  # All
    elif current_user.profile and current_user.profile.can_view_all_equipment:
        pass  # All
        
    elif current_user.profile and current_user.profile.can_view_battalion_realtime:
        if user_hierarchy:
            parts = user_hierarchy.split('/')
            if len(parts) >= 2:
                bat_path = "/".join(parts[:2])
                q = q.filter(models.Equipment.unit_hierarchy.startswith(bat_path))
            else:
                q = q.filter(models.Equipment.unit_hierarchy.startswith(user_hierarchy))
        else:
            q = q.filter(models.Equipment.holder_user_id == current_user.id)

    elif current_user.profile and current_user.profile.can_view_company_realtime:
        if user_hierarchy:
            q = q.filter(models.Equipment.unit_hierarchy.startswith(user_hierarchy))
        else:
            q = q.filter(models.Equipment.holder_user_id == current_user.id)
    else:
        q = q.filter(models.Equipment.holder_user_id == current_user.id)
    
    # 2. Optional text filter 
    if query_str:
        search = f"%{query_str}%"
        q = q.join(models.CatalogItem).filter(
            (models.CatalogItem.name.ilike(search)) |
            (models.Equipment.status.ilike(search))
        )
        
    items = q.order_by(models.Equipment.id.asc()).all()
    
    results = []
    for item in items:
        compliance = get_daily_status(item.last_verified_at)
        results.append(schemas.EquipmentResponse(
            id=item.id,
            type=item.item_name,
            item_name=item.item_name,
            status=item.status,
            current_state_description=item.current_state_description,
            compliance_check=item.report_status,
            report_status=item.report_status,
            compliance_level=compliance,
            holder_user_id=item.holder_user_id,
            custom_location=item.custom_location,
            actual_location_id=item.actual_location_id,
            serial_number=item.serial_number
        ))
    return results

@router.post("/equipment/", response_model=schemas.EquipmentResponse)
def create_equipment(
    item: schemas.EquipmentCreate, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    cat_item = db.query(models.CatalogItem).filter(models.CatalogItem.name == item.catalog_name).first()
    if not cat_item:
        cat_item = models.CatalogItem(name=item.catalog_name)
        db.add(cat_item)
        db.commit()
    
    new_item = models.Equipment(catalog_item_id=cat_item.id, serial_number=item.serial_number)
    db.add(new_item)
    db.commit()
    db.refresh(new_item)
    
    return schemas.EquipmentResponse(
        id=new_item.id,
        type=new_item.item_name,
        item_name=new_item.item_name,
        status=new_item.status,
        current_state_description=new_item.current_state_description,
        compliance_check=new_item.report_status,
        report_status=new_item.report_status,
        compliance_level=new_item.compliance_level,
        serial_number=new_item.serial_number,
        holder_user_id=new_item.holder_user_id,
        custom_location=new_item.custom_location,
        actual_location_id=new_item.actual_location_id
    )

@router.post("/equipment/assign_owner/")
def assign_owner(
    req: schemas.AssignOwnerRequest, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    allowed_tech_roles = ["Company Tech Soldier", "Battalion Tech Commander", "Brigade Tech Commander"]
    has_permission = (current_user.profile and current_user.profile.can_change_assignment_others) or \
                     (current_user.profile and current_user.profile.name in allowed_tech_roles) or \
                     current_user.role == "master"
                     
    if not has_permission:
        raise HTTPException(status_code=403, detail="Permission denied. Profile cannot assign owners.")

    item = db.query(models.Equipment).filter(models.Equipment.id == req.equipment_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    
    item.owner_user_id = req.owner_id
    item.holder_user_id = req.owner_id
    item.actual_location_id = None
    item.last_verified_at = datetime.utcnow()
    item.custom_location = None
    
    db.commit()
    return {"status": "Ownership Assigned", "state": item.current_state_description}

@router.post("/equipment/transfer")
def transfer_equipment(
    req: schemas.TransferPossessionRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """
    Transfer possession to a Person OR a Location (Strict XOR).
    """
    allowed_tech_roles = ["Company Tech Soldier", "Battalion Tech Commander", "Brigade Tech Commander"]
    has_permission = (current_user.profile and current_user.profile.can_change_assignment_others) or \
                     (current_user.profile and current_user.profile.name in allowed_tech_roles) or \
                     current_user.role == "master"

    if not has_permission:
        raise HTTPException(status_code=403, detail="Permission Denied: Cannot transfer equipment.")

    # XOR Validation
    if req.to_holder_id is None and req.to_location is None:
        raise HTTPException(status_code=400, detail="Must provide either to_holder_id or to_location.")
    if req.to_holder_id is not None and req.to_location is not None:
        raise HTTPException(status_code=400, detail="Cannot transfer to both Person and Location.")

    item = db.query(models.Equipment).filter(models.Equipment.id == req.equipment_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Equipment not found")
    
    try:
        if req.to_holder_id:
            target = db.query(models.User).filter(models.User.id == req.to_holder_id).first()
            if not target:
                raise HTTPException(status_code=404, detail="Target user not found")
                 
            item.holder_user_id = target.id
            item.custom_location = None
            
            log = models.TransactionLog(
                equipment_id=item.id,
                involved_user_id=current_user.id,
                event_type="HANDOVER",
                user_status_at_time=current_user.is_active_duty,
                location=f"User:{target.full_name}" 
            )
            db.add(log)
            result_msg = {"status": "Transferred", "new_holder": target.full_name}

        else:
            item.custom_location = req.to_location
            item.holder_user_id = None
            
            log = models.TransactionLog(
                equipment_id=item.id,
                involved_user_id=current_user.id,
                event_type="HANDOVER_LOC",
                user_status_at_time=current_user.is_active_duty,
                location=req.to_location
            )
            db.add(log)
            result_msg = {"status": "Transferred", "location": req.to_location}

        item.actual_location_id = None
        item.last_verified_at = datetime.utcnow()
        
        db.commit()
        db.refresh(item)
        return result_msg

    except Exception as e:
        db.rollback()
        print(f"Transfer Error: {e}")
        raise HTTPException(status_code=500, detail=f"Internal Server Error during transfer: {str(e)}")

@router.post("/equipment/{equipment_id}/verify")
def verify_equipment_daily(
    equipment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    item = db.query(models.Equipment).filter(models.Equipment.id == equipment_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Equipment not found")
        
    if item.holder_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Permission Denied: You can only verify equipment you hold.")
        
    item.last_verified_at = datetime.utcnow()
    
    trans_log = models.TransactionLog(
        equipment_id=item.id,
        involved_user_id=current_user.id,
        event_type="VERIFICATION",
        user_status_at_time=current_user.is_active_duty,
        timestamp=datetime.utcnow()
    )
    db.add(trans_log)
    
    db.commit()
    
    new_status = get_daily_status(item.last_verified_at)
    return {"status": "Verified", "compliance": new_status}
