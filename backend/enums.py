"""
Shared enumerated types.

Kept dependency-free so both the ORM models and the Pydantic schemas can
import it without a cycle.
"""
import enum


class EquipmentStatus(str, enum.Enum):
    """The statuses an equipment record is meant to hold.

    Enforced on the verification write path only: the column is still a plain
    String, and routers/maintenance.py assigns literals directly. Analytics
    counts readiness by matching FUNCTIONAL exactly (routers/analytics.py),
    so free-form values on the unconstrained paths still corrupt the metric.

    These are the four options the verification form offers
    (frontend VerificationForm.tsx); keep the two in sync.
    """
    FUNCTIONAL = "Functional"
    MALFUNCTIONING = "Malfunctioning"
    IN_REPAIR = "In Repair"
    MISSING = "Missing"
