/**
 * Shared TypeScript interfaces for the application
 */
import type { Capabilities } from '@/lib/capabilities';

// ============ USER & AUTH ============
export interface User {
    id: number;
    personal_number: string;
    full_name: string;
    is_active_duty: boolean;
    // Where the user sits, and the only statement of it (H1-12 -- role,
    // battalion and company are gone, along with Profile/UserRole).
    group?: Group;
}

export interface Group {
    id: number;
    name: string;
    kind: string;
    // DATA-M9-1. The IANA zone the group's calendar day is kept in.
    timezone: string;
}

// LoginCredentials and TokenResponse lived here and are gone with SEC-H9.
// TokenResponse described a token this client no longer receives, holds, or
// stores. LoginCredentials was never imported by anything and its shape
// ({ username, password }) disagreed with the live one in auth.service.ts
// ({ personalNumber, password }) -- two same-named types, one importable by
// mistake from '@/types'.

// SEC-H10. What resolveSession() answers with: the user AND what they may
// do, bundled so there is no render window where one exists without the
// other -- see lib/capabilities.ts for why `anywhere` is not a gate.
export interface Session {
    user: User;
    capabilities: Capabilities;
}

// ============ EQUIPMENT ============
// enums.ComplianceLevel in the backend, value for value; held to it by
// tests/test_compliance_vocabulary.py. Equipment.compliance_level and
// InventoryReportItem.reporting_status both carry it (DATA-M7).
export type ComplianceLevel = 'GOOD' | 'WARNING' | 'SEVERE';

export interface Equipment {
    id: number;
    type: string;
    item_name: string;
    status: string;
    current_state_description: string;
    compliance_check: string;
    report_status: string;
    // NEUTRAL is the frontend's own: no backend route sends it.
    compliance_level: ComplianceLevel | 'NEUTRAL';
    holder_user_id?: number;
    custom_location?: string;
    actual_location_id?: number;
    serial_number?: string;
}

export interface EquipmentCreateRequest {
    catalog_name: string;
    serial_number?: string;
}

export interface TransferRequest {
    equipment_id: number;
    to_holder_id?: number;
    to_location?: string;
    // DATA-M8-1. Optional free text; the backend trims it, turns a blank
    // into null and refuses more than 500 characters.
    reason?: string;
}

export interface AssignOwnerRequest {
    equipment_id: number;
    owner_id: number;
    reason?: string;
}

// ============ REPORTS ============
// One row of GET /reports/query, key for key (API-H4). Nothing generates
// this from the route's response model (schemas.InventoryReportItem, since
// DATA-M7); it is held to the route by tests/test_report_item_contract.py,
// which reads this block. Every key is always sent, and last_verified_at
// alone is null by design, for an item never verified. reporting_status is
// always computed; the other string fields fall back when the item's own
// column is NULL or a related row is missing -- but not when a related owner
// or holder EXISTS with a NULL full_name. That column is nullable, and since
// DATA-M7 such a row fails the response model, so the whole report answers
// 500 rather than sending a null. No write path creates such a user; the
// column, not this type, is where that gets fixed (DATA-L11).
export interface InventoryReportItem {
    id: number;
    item_type: string;
    unit_association: string;
    designated_owner: string;
    actual_location: string;
    serial_number: string;
    // The item's compliance level, the same value /equipment/ sends; the
    // report page shows a Hebrew label for it.
    reporting_status: ComplianceLevel;
    last_reporter: string;
    last_verified_at: string | null; // ISO-8601, Z-suffixed
}

// One row of GET /reports/daily_movement, key for key (DATA-M8-2), held to
// the route's response model (schemas.MovementReportItem) by
// tests/test_movement_report.py, which reads this block. Every key is
// always sent.
//
// The from_/to_ names are the custody an event left and the one it went
// to. They are null on every event that moves nothing, on a row from before
// DATA-M8-1, and for a person whose full_name is NULL. `location` is the
// other way round: only a row written before DATA-M8-2 has it.
export interface MovementReportItem {
    id: number;
    timestamp: string; // ISO-8601, Z-suffixed
    // An EventType value; DailyActivityTable's EVENT_META labels each one.
    event_type: string;
    serial_number: string | null;
    reporter_name: string | null;
    from_holder_name: string | null;
    to_holder_name: string | null;
    from_owner_name: string | null;
    to_owner_name: string | null;
    from_group_name: string | null;
    to_group_name: string | null;
    from_location: string | null;
    to_location: string | null;
    reason: string | null;
    location: string | null;
}

// GET /reports/daily_movement, key for key (DATA-M9-2): one group's calendar
// day. Held to the route's response model (schemas.MovementReport) by
// tests/test_movement_report.py, which reads this block. Every key is always
// sent.
export interface MovementReport {
    group_id: number;
    group_name: string;
    // The IANA zone that bounded the day, and the one to show its times in.
    timezone: string;
    date: string; // YYYY-MM-DD, the day in that zone
    start: string; // ISO-8601, Z-suffixed: the day's first instant
    end: string; // ISO-8601, Z-suffixed: the next day's first instant
    items: MovementReportItem[];
}

// ============ MAINTENANCE ============
export interface Ticket {
    id: number;
    equipment_id: number;
    equipment_name: string;
    fault_type: string;
    description: string;
    status: string;
    opened_at?: string | null;
    closed_at?: string;
}

export interface FaultType {
    id: number;
    name: string;
    is_pending: boolean;
}

export interface UnitReadiness {
    total_items: number;
    functional_items: number;
    readiness_percentage: number;
}
