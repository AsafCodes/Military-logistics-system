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
export interface Equipment {
    id: number;
    type: string;
    item_name: string;
    status: string;
    current_state_description: string;
    compliance_check: string;
    report_status: string;
    compliance_level: 'GOOD' | 'WARNING' | 'SEVERE' | 'NEUTRAL';
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
}

export interface AssignOwnerRequest {
    equipment_id: number;
    owner_id: number;
}

// ============ REPORTS ============
// One row of GET /reports/query, key for key (API-H4). The route builds a
// plain dict with no response model, so nothing generates this -- it is
// held to the route by tests/test_report_item_contract.py, which reads this
// block. Every key is always sent, and last_verified_at alone is null by
// design, for an item never verified. reporting_status is always computed;
// the other string fields fall back when the item's own column is NULL or a
// related row is missing -- but not when a related owner or holder EXISTS
// with a NULL full_name: that column is nullable, and designated_owner or
// last_reporter would forward its null. No write path creates such a user;
// the column, not this type, is where that gets fixed.
export interface InventoryReportItem {
    id: number;
    item_type: string;
    unit_association: string;
    designated_owner: string;
    actual_location: string;
    serial_number: string;
    // "Reported", or get_daily_status's "WARNING" / "SEVERE". Left a plain
    // string: the vocabulary itself is DATA-M7's to settle.
    reporting_status: string;
    last_reporter: string;
    last_verified_at: string | null; // ISO-8601, Z-suffixed
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
