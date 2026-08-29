/**
 * DATA-H1-3. DATA-H1's own evidence named this table by symptom: a naive
 * (zoneless) timestamp string parses in the BROWSER's local time, not UTC, so
 * every displayed hour was off by the viewer's offset. H1-1 fixed it by
 * making /reports/daily_movement emit an aware `Z`-suffixed string -- with no
 * change to this component, since `new Date(s)` already parses a `Z`-suffixed
 * string as the absolute instant regardless of runtime timezone.
 *
 * That correctness is silent: nothing here states the dependency on the `Z`
 * suffix, so a backend regression that reintroduced a zoneless string would
 * reshift this table again with nothing to notice. This test is that guard.
 *
 * It only means something under a non-UTC runtime timezone -- a naive string
 * and a `Z` string parse to the SAME instant when TZ=UTC, which is CI's
 * default, so a test that didn't force a non-UTC zone would pass whether or
 * not the `Z` is honored. Asia/Kolkata is +05:30 with no DST, so this can't
 * start failing seasonally. 09:30Z is chosen deliberately over a round UTC
 * hour: it renders as 15:00 IST, an unambiguous five-and-a-half-hour shift
 * that could not be mistaken for a rounding artifact of some other offset.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import DailyActivityTable from './DailyActivityTable';
import api from '@/api';

const ACTIVITY_ITEM = {
    id: 1,
    timestamp: '2026-06-15T09:30:00Z',
    event_type: 'movement',
    serial_number: 'M1-PROBE',
    reporter_name: null,
    location: 'Armory',
};

// A second, independently-chosen row for the multi-row test below. A
// single-row test cannot rule out a bug that happens to work for the one
// value under test (a memoized "now", an accidental single-shot parse
// outside the map()) -- two rows with two different correct answers can.
const SECOND_ACTIVITY_ITEM = {
    id: 2,
    timestamp: '2026-06-15T12:15:00Z',
    event_type: 'fault',
    serial_number: 'M2-PROBE',
    reporter_name: null,
    location: 'Warehouse',
};

afterEach(() => {
    vi.unstubAllEnvs();
});

describe('DailyActivityTable: renders the Z-suffixed timestamp in the viewer local time (DATA-H1-3)', () => {
    it('shows 15:00 for 09:30Z under Asia/Kolkata (+05:30)', async () => {
        vi.stubEnv('TZ', 'Asia/Kolkata');
        vi.spyOn(api, 'get').mockImplementation((url: string) => {
            if (url === '/reports/daily_movement') return Promise.resolve({ data: [ACTIVITY_ITEM] });
            throw new Error(`unexpected URL in test: ${url}`);
        });

        render(<DailyActivityTable />);

        expect(await screen.findByText('15:00')).toBeInTheDocument();
        // Not the naive misread of the same string -- see this file's docstring.
        expect(screen.queryByText('09:30')).toBeNull();
    });

    it('applies the same Z-aware parsing to every row, not just the first', async () => {
        vi.stubEnv('TZ', 'Asia/Kolkata');
        vi.spyOn(api, 'get').mockImplementation((url: string) => {
            if (url === '/reports/daily_movement') {
                return Promise.resolve({ data: [ACTIVITY_ITEM, SECOND_ACTIVITY_ITEM] });
            }
            throw new Error(`unexpected URL in test: ${url}`);
        });

        render(<DailyActivityTable />);

        expect(await screen.findByText('15:00')).toBeInTheDocument();
        expect(await screen.findByText('17:45')).toBeInTheDocument();
        expect(screen.queryByText('09:30')).toBeNull();
        expect(screen.queryByText('12:15')).toBeNull();
    });
});
