/**
 * FE-H5. A component that reads on mount must abort that read when it
 * unmounts, and must not report the aborted read as a failure.
 *
 * Each case renders a component with its GETs held open, apart from any the
 * case answers at once. It waits until exactly the expected reads are out,
 * so the test can't pass on a component that sent nothing, then unmounts.
 * Every read's signal must have aborted. Each held
 * request is then rejected the way axios rejects an aborted one, and nothing
 * may reach console.error or alert.
 *
 * React 19 ignores a setState after unmount, so this can't see a component
 * that skips its post-await check; it sees only the abort and the reporting.
 *
 * The second sweep mounts, under StrictMode, the five components that call a
 * useLatestRequest load straight from a mount effect. StrictMode runs a mount
 * effect twice in development, so the first load is aborted while the
 * component stays up, and there a state write does show: rejecting the
 * aborted load must leave the markup exactly as it was and log nothing.
 * AdminPanel's markup doesn't show its group loading until a user is
 * selected; AdminPanel.test.tsx covers that.
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
import AdminPanel from '@/features/dashboard/components/AdminPanel';
import EquipmentHistory from '@/features/equipment/components/EquipmentHistory';
import EquipmentPage from '@/features/equipment/components/EquipmentPage';
import MaintenancePage from '@/features/maintenance/components/MaintenancePage';
import FaultTypeQueuePage from '@/features/catalog/components/FaultTypeQueuePage';
import GeneralReportPage from '@/features/reports/components/GeneralReportPage';
import { TEST_USER } from '@/test/setup';

interface Case {
    name: string;
    ui: () => ReactElement;
    /** GETs answered at once; every other GET is held. */
    answers?: Record<string, unknown>;
    /** The held GETs, in the order they are sent. */
    urls: string[];
}

// Each of these owns a controller in its mount effect.
const EFFECT_CASES: Case[] = [
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

// Stage 2: /users/me answered, so the page is waiting on fetchData, which
// loads through useLatestRequest. The child components mount only after it,
// so none of theirs are out.
const DASHBOARD_STAGE_2: Case = {
    name: 'DashboardPage (stage 2)',
    ui: () => <DashboardPage onLogout={() => { }} />,
    answers: { '/users/me': TEST_USER },
    urls: ['/analytics/unit_readiness', '/equipment/accessible'],
};

// These call a load built on useLatestRequest straight from a mount effect.
// Its unmount effect aborts the signal the last load took.
const LATEST_REQUEST_CASES: Case[] = [
    {
        name: 'EquipmentPage',
        ui: () => <EquipmentPage />,
        urls: ['/users/me', '/equipment/accessible', '/setup/fault_types'],
    },
    {
        name: 'MaintenancePage',
        ui: () => <MaintenancePage />,
        urls: ['/tickets/'],
    },
    {
        name: 'FaultTypeQueuePage',
        ui: () => <FaultTypeQueuePage />,
        urls: ['/setup/fault_types/pending'],
    },
    {
        // The search is empty, so only /groups goes out.
        name: 'AdminPanel',
        ui: () => <AdminPanel onClose={() => { }} />,
        urls: ['/groups'],
    },
    {
        name: 'GeneralReportPage',
        ui: () => <GeneralReportPage />,
        urls: ['/reports/query'],
    },
];

const CASES: Case[] = [...EFFECT_CASES, DASHBOARD_STAGE_2, ...LATEST_REQUEST_CASES];

describe('reads on mount are aborted on unmount (FE-H5)', () => {
    it.each(CASES)('$name aborts its reads and reports nothing', async ({ ui, answers, urls }) => {
        const error = vi.spyOn(console, 'error').mockImplementation(() => { });
        const alerted = vi.spyOn(window, 'alert').mockImplementation(() => { });
        const { held } = holdGets(api, answers);

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

describe('an aborted first load under StrictMode changes nothing (FE-H5)', () => {
    it.each(LATEST_REQUEST_CASES)('$name keeps loading and reports nothing', async ({ ui, answers, urls }) => {
        const error = vi.spyOn(console, 'error').mockImplementation(() => { });
        const { held } = holdGets(api, answers);

        const { container } = render(ui(), { reactStrictMode: true });
        await waitFor(() => expect(held.map(h => h.url)).toEqual([...urls, ...urls]));
        expect(held.map(h => h.config?.signal?.aborted))
            .toEqual([...urls.map(() => true), ...urls.map(() => false)]);

        const before = container.innerHTML;
        await act(async () => { for (const h of held.slice(0, urls.length)) h.reject(new CanceledError()); });

        expect(container.innerHTML).toBe(before);
        expect(error).not.toHaveBeenCalled();
    });
});
