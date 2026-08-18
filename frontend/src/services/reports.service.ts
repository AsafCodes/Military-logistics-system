/**
 * Reports Service
 * Handles inventory reports and daily movement reports
 */
import apiClient from '@/lib/axios';
import type { InventoryReportFilters, InventoryReportItem } from '@/types';

interface DailyMovementItem {
    id: number;
    timestamp: string;
    event_type: string;
    serial_number?: string;
    reporter_name?: string;
    location?: string;
}

class ReportsService {
    /**
     * Get inventory report with optional filters
     */
    async getInventoryReport(filters?: InventoryReportFilters): Promise<InventoryReportItem[]> {
        const params: Record<string, string> = {};

        if (filters?.equipment_type) params.equipment_type = filters.equipment_type;
        if (filters?.location) params.location = filters.location;
        if (filters?.status) params.status = filters.status;
        if (filters?.holder_name) params.holder_name = filters.holder_name;

        const response = await apiClient.get<InventoryReportItem[]>('/reports/query', { params });
        return response.data;
    }

    /**
     * Get daily movement report (last 24 hours)
     */
    async getDailyMovement(): Promise<DailyMovementItem[]> {
        const response = await apiClient.get<DailyMovementItem[]>('/reports/daily_movement');
        return response.data;
    }
}

// Export singleton instance
export const reportsService = new ReportsService();
export default reportsService;
