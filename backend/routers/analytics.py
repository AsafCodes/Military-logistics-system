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
    #
    # DATA-H3-3 widened what "can see" means and this route was left alone ON
    # PURPOSE, which is a decision rather than an oversight. Classified items in
    # your scope now drop out of BOTH counts unless you hold VIEW_CLASSIFIED
    # over their group, so an uncleared caller's total silently shrinks. Routing
    # around the classification filter here would keep the number truthful at
    # the cost of a SECOND definition of visibility -- the DATA-H9 shape -- and
    # would be the only place in the backend where scoping means something
    # different. The number continues to mean exactly what the paragraph above
    # says it means: the readiness of what you can see.
    #
    # The accepted cost, named so nobody discovers it as a surprise: a cleared
    # and an uncleared caller comparing totals can infer that a classified item
    # exists in that scope, though not which one. Pinned by a test in
    # tests/test_sensitivity_contract.py so the behaviour cannot drift quietly.
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
