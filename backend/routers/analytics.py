"""Analytics Router - Unit readiness endpoint"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..dependencies import get_current_active_user

router = APIRouter(tags=["analytics"])


@router.get("/analytics/unit_readiness")
def get_unit_readiness(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user),
):
    total = db.query(models.Equipment).count()
    functional = (
        db.query(models.Equipment)
        .filter(models.Equipment.status == "Functional")
        .count()
    )

    readiness = (functional / total * 100) if total > 0 else 0

    return {
        "total_items": total,
        "functional_items": functional,
        "readiness_percentage": round(readiness, 2),
    }
