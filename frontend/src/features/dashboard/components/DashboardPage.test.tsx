/**
 * FE-H5. The dashboard's first read, /users/me, is aborted on unmount, and an
 * aborted read reports nothing (src/abortOnUnmount.test.tsx). These pin the
 * other half: an answered read still shows the page, and a read that fails
 * for real still shows the error card.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
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

        const { container } = render(<DashboardPage onLogout={() => { }} />);

        expect(await screen.findByText(new RegExp(TEST_USER.full_name))).toBeInTheDocument();
        await waitFor(() => expect(container.querySelector('.animate-spin')).toBeNull());
        expect(screen.queryByText(INIT_ERROR)).toBeNull();
    });

    it('still logs a real failure and shows the error card', async () => {
        const error = vi.spyOn(console, 'error').mockImplementation(() => { });
        const { held } = holdGets(api, DATA_LOADS);

        render(<DashboardPage onLogout={() => { }} />);
        await waitFor(() => expect(held.map(h => h.url)).toEqual(['/users/me']));
        const failure = new Error('timeout of 10000ms exceeded');
        held[0].reject(failure);

        expect(await screen.findByText(INIT_ERROR)).toBeInTheDocument();
        expect(error).toHaveBeenCalledWith('Failed to init', failure);
    });
});
