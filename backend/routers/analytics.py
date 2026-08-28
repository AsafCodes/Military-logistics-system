"""Analytics Router - Unit readiness endpoint"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_active_user, scope_equipment_query
from .. import models

router = APIRouter(tags=["analytics"])

@router.get("/analytics/unit_readiness")
def get_unit_readiness(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    # SEC-H5. This counted the whole force for everyone, so a private read the
    # army's readiness posture off the dashboard. Both counts now run through
    # the same scope every other equipment read uses: the number means YOUR
    # readiness, and a commander's figure is the readiness of what they
    # command. The percentage was never comparable across users anyway -- it
    # just looked like it was.
    visible = scope_equipment_query(db.query(models.Equipment), current_user)
    total = visible.count()
    functional = scope_equipment_query(
        db.query(models.Equipment).filter(models.Equipment.status == "Functional"),
        current_user,
    ).count()
    
    readiness = (functional / total * 100) if total > 0 else 0
    
    return {
        "total_items": total,
        "functional_items": functional,
        "readiness_percentage": round(readiness, 2)
    }
