/**
 * FE-H5. A component that reads on mount must abort that read when it
 * unmounts, and must not report the aborted read as a failure.
 *
 * Each case renders a component with every GET held open, waits until exactly
 * the expected reads are out, so the test can't pass on a component that sent
 * nothing, then unmounts. Every read's signal must have aborted. Each held
 * request is then rejected the way axios rejects an aborted one, and nothing
 * may reach console.error or alert.
 *
 * React 19 ignores a setState after unmount, so this can't see a component
 * that skips its post-await check; it sees only the abort and the reporting.
 */
import type { ReactElement } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, waitFor, act } from '@testing-library/react';
import { CanceledError } from 'axios';
import api from '@/lib/axios';
import { holdGets } from '@/test/httpStubs';
import StatsGrid from '@/features/dashboard/components/StatsGrid';
import DailyActivityTable from '@/features/dashboard/components/DailyActivityTable';
import DashboardPage from '@/features/dashboard/components/DashboardPage';
import EquipmentHistory from '@/features/equipment/components/EquipmentHistory';

interface Case {
    name: string;
    ui: () => ReactElement;
    urls: string[];
}

const CASES: Case[] = [
    {
        name: 'StatsGrid',
        ui: () => <StatsGrid stats={{ readiness_percentage: 80, total_items: 5, functional_items: 4 }} />,
        urls: ['/tickets/?status_filter=Open'],
    },
    {
        name: 'DailyActivityTable',
        ui: () => <DailyActivityTable />,
        urls: ['/reports/daily_movement'],
    },
    {
        name: 'EquipmentHistory',
        ui: () => <EquipmentHistory equipmentId={7} isOpen onClose={() => { }} />,
        urls: ['/equipment/7/history'],
    },
    {
        // Stage 1 only: the page's data reads wait on /users/me, which is held.
        name: 'DashboardPage',
        ui: () => <DashboardPage onLogout={() => { }} />,
        urls: ['/users/me'],
    },
];

describe('reads on mount are aborted on unmount (FE-H5)', () => {
    it.each(CASES)('$name aborts its reads and reports nothing', async ({ ui, urls }) => {
        const error = vi.spyOn(console, 'error').mockImplementation(() => { });
        const alerted = vi.spyOn(window, 'alert').mockImplementation(() => { });
        const { held } = holdGets(api);

        const { unmount } = render(ui());
        await waitFor(() => expect(held.map(h => h.url)).toEqual(urls));
        expect(held.map(h => h.config?.signal?.aborted)).toEqual(urls.map(() => false));

        unmount();
        expect(held.map(h => h.config?.signal?.aborted)).toEqual(urls.map(() => true));

        for (const h of held) h.reject(new CanceledError());
        await act(async () => { });

        expect(error).not.toHaveBeenCalled();
        expect(alerted).not.toHaveBeenCalled();
    });
});
