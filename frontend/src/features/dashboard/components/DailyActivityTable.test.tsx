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
import { renderToStaticMarkup } from 'react-dom/server';
import DailyActivityTable from './DailyActivityTable';
import api from '@/lib/axios';

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


/**
 * DATA-H4-1. The label switch, against the vocabulary the backend actually
 * emits rather than the vocabulary this file was written against.
 *
 * The fixtures above use 'movement' and 'fault'. Neither string has ever been
 * written by any router: backend/enums.py EventType emits HANDOVER,
 * HANDOVER_LOC, VERIFICATION, FIX and (new with this ticket) ASSIGN. So the
 * two switches in this component were being exercised entirely on values that
 * do not occur, while every real handover fell through to `default` and
 * printed raw English into an RTL Hebrew table -- for as long as the feature
 * has existed, with this suite green.
 *
 * tests/test_audit_trail.py has a guard that every EventType value has a
 * `case` arm here, but a substring check on a source file cannot prove React
 * renders it. This is the half that runs the component.
 *
 * The negative assertion is the one that would have caught the original bug:
 * asserting the Hebrew appears says a label was found, while asserting the
 * raw event_type is ABSENT says the `default` arm was not taken.
 */
describe('DailyActivityTable: labels the event types the backend really writes (DATA-H4-1)', () => {
    const CASES: Array<[string, string]> = [
        ['HANDOVER', 'העברה'],
        ['HANDOVER_LOC', 'העברה למיקום'],
        ['ASSIGN', 'שינוי בעלות'],
        ['VERIFICATION', 'אימות'],
        ['FIX', 'תיקון'],
    ];

    it.each(CASES)('renders %s as a Hebrew label, not raw text', async (eventType, label) => {
        vi.spyOn(api, 'get').mockImplementation((url: string) => {
            if (url === '/reports/daily_movement') {
                return Promise.resolve({
                    data: [{ ...ACTIVITY_ITEM, event_type: eventType }],
                });
            }
            throw new Error(`unexpected URL in test: ${url}`);
        });

        render(<DailyActivityTable />);

        expect(await screen.findByText(label)).toBeInTheDocument();
        expect(screen.queryByText(eventType)).not.toBeInTheDocument();
    });
});

/**
 * FE-H5. The read is aborted on unmount and an aborted read reports nothing
 * (src/abortOnUnmount.test.tsx). This is the other half: a read that fails
 * for real still logs and shows the error.
 */
describe('DailyActivityTable: a real failure is still reported (FE-H5)', () => {
    it('logs the failure and shows the error message', async () => {
        const error = vi.spyOn(console, 'error').mockImplementation(() => { });
        const failure = new Error('timeout of 10000ms exceeded');
        vi.spyOn(api, 'get').mockRejectedValue(failure);

        render(<DailyActivityTable />);

        expect(await screen.findByText('טעינת יומן הפעילות נכשלה')).toBeInTheDocument();
        expect(error).toHaveBeenCalledWith('Failed to fetch daily activity', failure);
    });
});

/**
 * FE-H6-2. The read's `finally` became a statement after the try/catch, and
 * the check on the response's shape moved into a helper.
 */
describe('DailyActivityTable: loading and the response shapes (FE-H6-2)', () => {
    const LOADING = 'טוען פעילות...';
    const EMPTY = 'אין פעילות מתועדת ב-24 השעות האחרונות';
    const answerWith = (data: unknown) => vi.spyOn(api, 'get').mockResolvedValue({ data });

    it('shows loading in the first frame, before any effect has run', () => {
        expect(renderToStaticMarkup(<DailyActivityTable />)).toContain(LOADING);
    });

    it('stops loading once a flat array has answered', async () => {
        answerWith([ACTIVITY_ITEM]);
        render(<DailyActivityTable />);
        expect(await screen.findByText(ACTIVITY_ITEM.serial_number)).toBeInTheDocument();
        expect(screen.queryByText(LOADING)).toBeNull();
    });

    it('also reads rows from an { items } body', async () => {
        answerWith({ items: [ACTIVITY_ITEM] });
        render(<DailyActivityTable />);
        expect(await screen.findByText(ACTIVITY_ITEM.serial_number)).toBeInTheDocument();
    });

    it.each([[null], [{}], [{ items: null }]])('treats a body of %j as no activity', async (body) => {
        answerWith(body);
        render(<DailyActivityTable />);
        expect(await screen.findByText(EMPTY)).toBeInTheDocument();
    });
});
