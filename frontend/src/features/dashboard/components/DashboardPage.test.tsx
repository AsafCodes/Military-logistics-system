/**
 * FE-H5. The dashboard's first read, /users/me, is aborted on unmount, and an
 * aborted read reports nothing (src/abortOnUnmount.test.tsx). These pin the
 * other half: an answered read still shows the page, and a read that fails
 * for real still shows the error card.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { renderToStaticMarkup } from 'react-dom/server';
import { CanceledError } from 'axios';
import DashboardPage from './DashboardPage';
import api from '@/lib/axios';
import { holdGets } from '@/test/httpStubs';
import { TEST_USER } from '@/test/setup';

// The page's data reads and its two child components' reads, all answered.
const DATA_LOADS = {
    '/analytics/unit_readiness': { readiness_percentage: 80, total_items: 5, functional_items: 4 },
    '/equipment/accessible': [],
    '/tickets/?status_filter=Open': [],
    '/reports/daily_movement': [],
};

const INIT_ERROR = 'טעינת לוח הבקרה נכשלה. נסה לרענן.';

describe('DashboardPage: the first read (FE-H5)', () => {
    it("shows the page once /users/me answers", async () => {
        holdGets(api, { '/users/me': TEST_USER, ...DATA_LOADS });

        const { container } = render(<DashboardPage />);

        expect(await screen.findByText(new RegExp(TEST_USER.full_name))).toBeInTheDocument();
        await waitFor(() => expect(container.querySelector('.animate-spin')).toBeNull());
        expect(screen.queryByText(INIT_ERROR)).toBeNull();
    });

    it('still logs a real failure and shows the error card', async () => {
        const error = vi.spyOn(console, 'error').mockImplementation(() => { });
        const { held } = holdGets(api, DATA_LOADS);

        render(<DashboardPage />);
        await waitFor(() => expect(held.map(h => h.url)).toEqual(['/users/me']));
        const failure = new Error('timeout of 10000ms exceeded');
        held[0].reject(failure);

        expect(await screen.findByText(INIT_ERROR)).toBeInTheDocument();
        expect(error).toHaveBeenCalledWith('Failed to init', failure);
    });
});

/**
 * FE-H6-2. The first load moved inside its effect and its `finally` became a
 * statement after the try/catch. The spinner still covers both stages.
 */
describe('DashboardPage: the spinner covers the whole first load (FE-H6-2)', () => {
    it('shows only the spinner in the first frame, before any effect has run', () => {
        const markup = renderToStaticMarkup(<DashboardPage />);
        expect(markup).toContain('animate-spin');
        expect(markup).not.toContain('רענן');
    });

    it('keeps the spinner while the data reads are out, and drops it when they answer', async () => {
        const { held } = holdGets(api, {
            '/users/me': TEST_USER,
            '/tickets/?status_filter=Open': [],
            '/reports/daily_movement': [],
        });
        const { container } = render(<DashboardPage />);
        await waitFor(() => expect(held).toHaveLength(2));
        await act(async () => { });

        expect(container.querySelector('.animate-spin')).not.toBeNull();
        expect(screen.queryByText(new RegExp(TEST_USER.full_name))).toBeNull();

        await act(async () => {
            held[0].resolve(DATA_LOADS['/analytics/unit_readiness']);
            held[1].resolve([]);
        });
        expect(screen.getByText(new RegExp(TEST_USER.full_name))).toBeInTheDocument();
    });

    it('drops the spinner when a data read fails, and shows that error', async () => {
        vi.spyOn(console, 'error').mockImplementation(() => { });
        const { held } = holdGets(api, { '/users/me': TEST_USER });
        const { container } = render(<DashboardPage />);
        await waitFor(() => expect(held).toHaveLength(2));

        await act(async () => { held[0].reject(new Error('timeout of 10000ms exceeded')); });

        expect(container.querySelector('.animate-spin')).toBeNull();
        expect(screen.getByText('Failed to load system data.')).toBeInTheDocument();
    });
});

// The Refresh button stays clickable during a load (useDashboardData's
// `loading` is never read), so two presses race.
describe('DashboardPage: Refresh twice, the latest load wins (FE-H5)', () => {
    const DATA_ERROR = 'Failed to load system data.';
    const item = (id: number, type: string) => ({
        id, type, serial_number: `S${id}`, status: 'Functional', compliance_level: 'GOOD',
    });

    it('a cancelled first pair reports nothing, and the second pair is shown', async () => {
        const error = vi.spyOn(console, 'error').mockImplementation(() => { });
        // /users/me and the child components' reads answer at once; the
        // page's own pair is held.
        const { held } = holdGets(api, {
            '/users/me': TEST_USER,
            '/tickets/?status_filter=Open': [],
            '/reports/daily_movement': [],
        });
        render(<DashboardPage />);

        const PAIR = ['/analytics/unit_readiness', '/equipment/accessible'];
        await waitFor(() => expect(held.map(h => h.url)).toEqual(PAIR));
        held[0].resolve(DATA_LOADS['/analytics/unit_readiness']);
        held[1].resolve([item(1, 'Mount Item')]);
        expect(await screen.findByText('Mount Item')).toBeInTheDocument();

        fireEvent.click(screen.getByTitle('רענן נתונים'));
        fireEvent.click(screen.getByTitle('רענן נתונים'));
        expect(held.map(h => h.url)).toEqual([...PAIR, ...PAIR, ...PAIR]);
        const [first, second] = [held.slice(2, 4), held.slice(4, 6)];
        expect([...first, ...second].map(h => h.config?.signal?.aborted)).toEqual([true, true, false, false]);

        await act(async () => { for (const h of first) h.reject(new CanceledError()); });
        expect(screen.queryByText(DATA_ERROR)).toBeNull();
        expect(error).not.toHaveBeenCalled();

        await act(async () => {
            second[0].resolve(DATA_LOADS['/analytics/unit_readiness']);
            second[1].resolve([item(2, 'Refreshed Item')]);
        });
        expect(screen.getByText('Refreshed Item')).toBeInTheDocument();
        expect(screen.queryByText('Mount Item')).toBeNull();
        expect(screen.queryByText(DATA_ERROR)).toBeNull();
    });
});
