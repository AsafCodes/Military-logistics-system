/**
 * FE-H4. useAnimatedCounter's own tests pin the cleanup. This one checks that
 * StatsGrid's four counters leave no frame pending when it unmounts
 * mid-animation, which fails if any counter keeps a private copy of the old
 * uncleaned loop.
 *
 * Only requestAnimationFrame/cancelAnimationFrame are faked, so waitFor still
 * polls on real timers while the open-tickets fetch resolves.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, waitFor, act } from '@testing-library/react';
import StatsGrid from './StatsGrid';
import api from '@/lib/axios';

beforeEach(() => {
    vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame'] });
});

afterEach(() => {
    vi.useRealTimers();
});

describe('StatsGrid', () => {
    it('leaves no animation frame pending when unmounted mid-animation', async () => {
        vi.spyOn(api, 'get').mockResolvedValue({ data: [{ id: 1 }, { id: 2 }, { id: 3 }] });

        const { unmount } = render(
            <StatsGrid stats={{ readiness_percentage: 83.33, total_items: 12, functional_items: 10 }} />,
        );
        // Three counters start at once; open tickets starts at 0 and waits for the fetch.
        expect(vi.getTimerCount()).toBe(3);
        await waitFor(() => expect(vi.getTimerCount()).toBe(4));

        for (let i = 0; i < 6; i++) {
            act(() => { vi.advanceTimersByTime(16); });
        }
        expect(vi.getTimerCount()).toBe(4);

        unmount();
        expect(vi.getTimerCount()).toBe(0);
    });

    // DATA-M22. A ticket someone took is still an unresolved fault, so the
    // card counts every ticket that is not Closed, not status Open alone.
    it('counts every ticket that is not closed as an open fault', async () => {
        const get = vi.spyOn(api, 'get').mockResolvedValue({
            data: [
                { id: 1, status: 'Open' },
                { id: 2, status: 'In Progress' },
                { id: 3, status: 'Waiting for Parts' },
                { id: 4, status: 'Closed' },
                { id: 5, status: 'Closed' },
            ],
        });

        const { getByText } = render(
            <StatsGrid stats={{ readiness_percentage: 80, total_items: 5, functional_items: 4 }} />,
        );
        await waitFor(() => expect(vi.getTimerCount()).toBe(4));
        // Past the counter's 1200 ms, one 16 ms frame at a time.
        for (let i = 0; i < 100; i++) {
            act(() => { vi.advanceTimersByTime(16); });
        }

        expect(get).toHaveBeenCalledTimes(1);
        expect(get.mock.calls[0][0]).toBe('/tickets/');
        const card = getByText('תקלות פתוחות').closest('.glass-card');
        expect(card?.querySelector('.text-3xl')?.textContent).toBe('3');
    });
});
