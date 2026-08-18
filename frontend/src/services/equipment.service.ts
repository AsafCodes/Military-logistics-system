/**
 * Equipment Service
 * Handles equipment CRUD, transfers, and verification
 */
import apiClient from '@/lib/axios';
import type { Equipment, EquipmentCreateRequest, TransferRequest, AssignOwnerRequest } from '@/types';

class EquipmentService {
    /**
     * Get all equipment accessible to current user (Matrix Security applied)
     */
    async getAllAccessible(queryStr?: string): Promise<Equipment[]> {
        const params = queryStr ? { query_str: queryStr } : {};
        const response = await apiClient.get<Equipment[]>('/equipment/accessible', { params });
        return response.data;
    }

    /**
     * Create new equipment
     */
    async create(data: EquipmentCreateRequest): Promise<Equipment> {
        const response = await apiClient.post<Equipment>('/equipment/', data);
        return response.data;
    }

    /**
     * Transfer equipment to a person or location (XOR)
     */
    async transfer(data: TransferRequest): Promise<{ status: string; new_holder?: string; location?: string }> {
        const response = await apiClient.post('/equipment/transfer', data);
        return response.data;
    }

    /**
     * Verify equipment (daily check)
     */
    async verify(equipmentId: number): Promise<{ status: string; compliance: string }> {
        const response = await apiClient.post(`/equipment/${equipmentId}/verify`);
        return response.data;
    }

    /**
     * Assign owner to equipment
     */
    async assignOwner(data: AssignOwnerRequest): Promise<{ status: string; state: string }> {
        const response = await apiClient.post('/equipment/assign_owner/', data);
        return response.data;
    }

    /**
     * Get my equipment only
     */
    async getMyEquipment(): Promise<Equipment[]> {
        const response = await apiClient.get<Equipment[]>('/users/me/equipment');
        return response.data;
    }
}

// Export singleton instance
export const equipmentService = new EquipmentService();
export default equipmentService;
