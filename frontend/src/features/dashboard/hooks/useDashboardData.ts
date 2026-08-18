import { useState, useCallback } from 'react';
import api from '@/api';
import type { User, UnitReadiness, Equipment } from '@/types';

export function useDashboardData() {
    const [stats, setStats] = useState<UnitReadiness | null>(null);
    const [equipment, setEquipment] = useState<Equipment[]>([]);
    const [loading, setLoading] = useState(false); // Default to false, controlled by fetchData
    const [error, setError] = useState<string | null>(null);

    const fetchData = useCallback(async (activeUser: User) => {
        setLoading(true);
        try {
            let equipmentEndpoint = '/users/me/equipment';
            if (activeUser.profile?.can_view_company_realtime || activeUser.profile?.can_view_battalion_realtime) {
                equipmentEndpoint = '/equipment/accessible';
            }

            const [readinessRes, equipmentRes] = await Promise.all([
                api.get('/analytics/unit_readiness'),
                api.get(equipmentEndpoint)
            ]);

            setStats(readinessRes.data);
            setEquipment(equipmentRes.data);
            setError(null);
        } catch (err: any) {
            console.error('Failed to fetch dashboard data:', err);
            setError('Failed to load system data.');
        } finally {
            setLoading(false);
        }
    }, []);

    // Manual refresh helper
    const refreshData = useCallback(async (activeUser: User | null) => {
        if (activeUser) await fetchData(activeUser);
    }, [fetchData]);

    return { stats, equipment, loading, error, fetchData, refreshData };
}
