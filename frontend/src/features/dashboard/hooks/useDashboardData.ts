import { useState, useCallback } from 'react';
import api from '@/lib/axios';
import { useLatestRequest } from '@/lib/useLatestRequest';
import type { User, UnitReadiness, Equipment } from '@/types';

export function useDashboardData() {
    const [stats, setStats] = useState<UnitReadiness | null>(null);
    const [equipment, setEquipment] = useState<Equipment[]>([]);
    const [loading, setLoading] = useState(false); // Default to false, controlled by fetchData
    const [error, setError] = useState<string | null>(null);

    // FE-H5: a load started by Refresh aborts the one still out, and leaving
    // the page aborts the last one, so only the latest load writes state.
    const next = useLatestRequest();

    const fetchData = useCallback(async () => {
        const signal = next();
        setLoading(true);
        try {
            // /equipment/accessible is scope_equipment_query's own rule: every
            // item in the caller's VIEW extent, OR held by the caller. That is
            // a superset of /users/me/equipment for every caller, including one
            // with no VIEW grant beyond their own holdings -- so there is no
            // profile check left to make here (H1-12 drops Profile entirely).
            const [readinessRes, equipmentRes] = await Promise.all([
                api.get('/analytics/unit_readiness', { signal }),
                api.get('/equipment/accessible', { signal })
            ]);
            if (signal.aborted) return;

            setStats(readinessRes.data);
            setEquipment(equipmentRes.data);
            setError(null);
        } catch (err: any) {
            if (signal.aborted) return;
            console.error('Failed to fetch dashboard data:', err);
            setError('Failed to load system data.');
        } finally {
            if (!signal.aborted) setLoading(false);
        }
    }, [next]);

    // Manual refresh helper
    const refreshData = useCallback(async (activeUser: User | null) => {
        if (activeUser) await fetchData();
    }, [fetchData]);

    return { stats, equipment, loading, error, fetchData, refreshData };
}
