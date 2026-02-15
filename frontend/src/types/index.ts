/**
 * Shared TypeScript interfaces for the application
 */

// ============ USER & AUTH ============
export interface User {
    id: number;
    personal_number: string;
    full_name: string;
    role: string;
    battalion?: string;
    company?: string;
    unit_path?: string;
    unit_hierarchy?: string;
    is_active_duty: boolean;
    profile?: Profile;
}

export interface Profile {
    id: number;
    name: string;
    name_he?: string;
    can_view_all_equipment: boolean;
    can_view_battalion_realtime: boolean;
    can_view_company_realtime: boolean;
    can_change_assignment_others: boolean;
    can_change_maintenance_status: boolean;
    can_manage_locations: boolean;
    can_add_category: boolean;
    can_add_specific_item: boolean;
    can_remove_category: boolean;
    can_remove_specific_item: boolean;
    can_assign_roles: boolean;
    holds_equipment: boolean;
    must_report_presence: boolean;
}

export interface LoginCredentials {
    username: string;
    password: string;
}

export interface TokenResponse {
    access_token: string;
    token_type: string;
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
export interface InventoryReportFilters {
    equipment_type?: string;
    location?: string;
    status?: string;
    holder_name?: string;
}

export interface InventoryReportItem {
    id: number;
    serial_number?: string;
    item_name: string;
    status: string;
    holder_name?: string;
    location?: string;
    unit_hierarchy?: string;
    last_verified?: string;
}

// ============ MAINTENANCE ============
export interface Ticket {
    id: number;
    equipment_id: number;
    equipment_name: string;
    fault_type: string;
    description: string;
    status: string;
    opened_at: string;
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
