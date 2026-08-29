/**
 * DATA-H1-3. calculateDelay is the function DATA-H1's own evidence named:
 * elapsed-time ARITHMETIC against `last_verified_at`, not just display, so a
 * misparsed timestamp doesn't just print wrong -- it changes the computed day
 * count. H1-1 fixed the underlying contract (every timestamp now carries a
 * `Z`), with no change needed here, since `new Date(s)` already parses a
 * `Z`-suffixed string as the absolute instant regardless of runtime timezone.
 * This test pins that dependency so a backend regression back to a zoneless
 * string would be caught here rather than silently reshifting the table again.
 *
 * calculateDelay is module-local (not exported) -- the same situation
 * EquipmentPage.test.tsx documents for EquipmentRow -- so this renders the
 * whole page and asserts on rendered text rather than calling the helper.
 *
 * Needs a non-UTC runtime timezone to mean anything: a naive string and a `Z`
 * string parse to the same instant under UTC, which is CI's default, so a
 * test that didn't force a non-UTC zone would pass whether or not the `Z` is
 * honored. Asia/Kolkata (+05:30, no DST) again, for the same reason as
 * DailyActivityTable.test.tsx.
 *
 * "Now" is frozen with vi.setSystemTime alone, deliberately WITHOUT
 * vi.useFakeTimers(): this component's data fetch is a real awaited promise,
 * and Testing Library's findByText polls for it on a real setInterval. Fake
 * timers freeze that polling right along with the applicaton clock, so
 * findByText can never observe its own advance and hangs until vitest's test
 * timeout. setSystemTime alone overrides only `Date`, leaving
 * setTimeout/Promise scheduling untouched, so the real fetch and the real
 * polling both keep running -- only what `new Date()` returns changes.
 *
 * The seed instant (2026-06-10T04:00:00Z, four days twenty hours before the
 * frozen "now") is deliberately NOT a round day offset: read naively as local
 * Kolkata time it is five-and-a-half hours EARLIER, pushing the elapsed time
 * to 5 days 1.5 hours -- a day boundary crossing, so the correct and the
 * misparsed readings render visibly different Hebrew strings (4 vs 5 ימים)
 * rather than an invisible sub-day difference a floor() would swallow.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import GeneralReportPage from './GeneralReportPage';
import api from '@/api';

const LATE_ITEM = {
    id: 1,
    item_type: 'Rifle',
    unit_association: '188/53/A',
    designated_owner: 'Test Soldier',
    actual_location: 'Armory',
    serial_number: 'H1-3-PROBE',
    // Anything but 'Reported' -- GeneralReportPage.tsx:296 only renders
    // calculateDelay's output on the non-Reported branch.
    reporting_status: 'Late',
    last_reporter: 'Test Soldier',
    last_verified_at: '2026-06-10T04:00:00Z',
};

// calculateDelay has two Z-dependent branches -- diffDays>0 (LATE_ITEM,
// above) and diffHours>0, reached only when the elapsed time is under a day.
// The hours branch does the identical epoch-difference arithmetic on a
// different part of the same computation, so it needs its own pin: a
// regression isolated to that branch would be invisible to a days-only test.
// Same seed-selection discipline as LATE_ITEM -- read naively under
// Asia/Kolkata the instant is 5.5h earlier, correct=3h elapsed vs.
// naive=8h elapsed, both comfortably inside the hours branch on either side
// so the divergence is legible as one clean number, not a boundary crossing
// into a different branch.
const RECENT_ITEM = {
    ...LATE_ITEM,
    id: 2,
    serial_number: 'H1-3-PROBE-HOURS',
    last_verified_at: '2026-06-14T21:00:00Z',
};

afterEach(() => {
    vi.unstubAllEnvs();
    vi.useRealTimers(); // undoes setSystemTime; no-op if fake timers were never installed
});

describe('GeneralReportPage: calculateDelay reads the Z-suffixed timestamp as UTC (DATA-H1-3)', () => {
    it('shows 4 days elapsed, not the 5 a naive local-time misparse would give', async () => {
        vi.stubEnv('TZ', 'Asia/Kolkata');
        vi.setSystemTime(new Date('2026-06-15T00:00:00Z'));

        vi.spyOn(api, 'get').mockImplementation((url: string) => {
            if (url === '/reports/query') return Promise.resolve({ data: [LATE_ITEM] });
            throw new Error(`unexpected URL in test: ${url}`);
        });

        render(<GeneralReportPage />);

        expect(await screen.findByText('לפני 4 ימים')).toBeInTheDocument();
        expect(screen.queryByText('לפני 5 ימים')).toBeNull();
    });

    it('shows 3 hours elapsed on the hours branch, not the 8 a naive misparse would give', async () => {
        vi.stubEnv('TZ', 'Asia/Kolkata');
        vi.setSystemTime(new Date('2026-06-15T00:00:00Z'));

        vi.spyOn(api, 'get').mockImplementation((url: string) => {
            if (url === '/reports/query') return Promise.resolve({ data: [RECENT_ITEM] });
            throw new Error(`unexpected URL in test: ${url}`);
        });

        render(<GeneralReportPage />);

        expect(await screen.findByText('לפני 3 שעות')).toBeInTheDocument();
        expect(screen.queryByText('לפני 8 שעות')).toBeNull();
    });
});
