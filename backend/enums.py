"""
Shared enumerated types.

Kept dependency-free so both the ORM models and the Pydantic schemas can
import it without a cycle.
"""
import enum


class EquipmentStatus(str, enum.Enum):
    """The only statuses an equipment record may hold.

    Analytics counts readiness by matching FUNCTIONAL exactly
    (routers/analytics.py), so free-form values silently corrupt the metric.

    These are the four options the verification form offers
    (frontend VerificationForm.tsx); keep the two in sync.
    """
    FUNCTIONAL = "Functional"
    MALFUNCTIONING = "Malfunctioning"
    IN_REPAIR = "In Repair"
    MISSING = "Missing"
