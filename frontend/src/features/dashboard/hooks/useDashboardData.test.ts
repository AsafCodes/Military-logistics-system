/**
 * FE-H5. useDashboardData's fetchData is started by the dashboard's init and
 * by its Refresh button, which stays clickable during a load it started.
 * DashboardPage never reads `loading`, so the hook is driven directly here.
 */
import { describe, it, expect, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { CanceledError } from 'axios';
import { useDashboardData } from './useDashboardData';
import api from '@/lib/axios';
import { holdGets } from '@/test/httpStubs';

const STATS = { readiness_percentage: 80, total_items: 5, functional_items: 4 };
const item = (id: number) => ({ id, type: `Item ${id}`, serial_number: `S${id}` });

// Start two loads back to back; each holds a readiness and an equipment read.
async function twoLoads() {
    const error = vi.spyOn(console, 'error').mockImplementation(() => { });
    const { held } = holdGets(api);
    const { result } = renderHook(() => useDashboardData());
    await act(async () => {
        void result.current.fetchData();
        void result.current.fetchData();
    });
    expect(held).toHaveLength(4);
    expect(held.map(h => h.config?.signal?.aborted)).toEqual([true, true, false, false]);
    return { error, result, first: held.slice(0, 2), second: held.slice(2) };
}

describe('useDashboardData: the latest load wins (FE-H5)', () => {
    it('a cancelled first load leaves loading on and sets no error', async () => {
        const { error, result, first, second } = await twoLoads();

        await act(async () => { for (const h of first) h.reject(new CanceledError()); });
        expect(result.current.loading).toBe(true);
        expect(result.current.error).toBeNull();
        expect(error).not.toHaveBeenCalled();

        await act(async () => {
            second[0].resolve(STATS);
            second[1].resolve([item(2)]);
        });
        expect(result.current.loading).toBe(false);
        expect(result.current.stats).toEqual(STATS);
        expect(result.current.equipment).toEqual([item(2)]);
    });

    it("a first load that answers after the second keeps the second's data", async () => {
        const { result, first, second } = await twoLoads();

        await act(async () => {
            second[0].resolve(STATS);
            second[1].resolve([item(2)]);
        });
        await act(async () => {
            first[0].resolve({ ...STATS, total_items: 99 });
            first[1].resolve([item(1)]);
        });

        expect(result.current.stats).toEqual(STATS);
        expect(result.current.equipment).toEqual([item(2)]);
    });

    it('a real failure of the latest load is reported and ends loading', async () => {
        const { error, result, second } = await twoLoads();
        const failure = new Error('boom');

        await act(async () => { second[0].reject(failure); });

        expect(result.current.loading).toBe(false);
        expect(result.current.error).toBe('Failed to load system data.');
        expect(error).toHaveBeenCalledWith('Failed to fetch dashboard data:', failure);
    });

    it('a failed load recovers on the next one', async () => {
        vi.spyOn(console, 'error').mockImplementation(() => { });
        const { held } = holdGets(api);
        const { result } = renderHook(() => useDashboardData());

        await act(async () => { void result.current.fetchData(); });
        await act(async () => { held[0].reject(new Error('boom')); });
        expect(result.current.error).not.toBeNull();

        await act(async () => { void result.current.fetchData(); });
        await act(async () => {
            held[2].resolve(STATS);
            held[3].resolve([item(3)]);
        });
        expect(result.current.error).toBeNull();
        expect(result.current.equipment).toEqual([item(3)]);
    });
});
