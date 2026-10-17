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
import { describeMove } from '../describeMove';
import api from '@/lib/axios';
import type { MovementReportItem } from '@/types';

// Every key the route sends, each one empty. Each fixture below spreads it
// and sets what it is about.
const BLANK_ROW = {
    id: 0,
    timestamp: '2026-06-15T09:30:00Z',
    event_type: 'FAULT',
    serial_number: null,
    reporter_name: null,
    from_holder_name: null,
    to_holder_name: null,
    from_owner_name: null,
    to_owner_name: null,
    from_group_name: null,
    to_group_name: null,
    from_location: null,
    to_location: null,
    reason: null,
    location: null,
} satisfies MovementReportItem;

const ACTIVITY_ITEM = {
    ...BLANK_ROW,
    id: 1,
    timestamp: '2026-06-15T09:30:00Z',
    event_type: 'movement',
    serial_number: 'M1-PROBE',
    location: 'Armory',
} satisfies MovementReportItem;

// A second, independently-chosen row for the multi-row test below. A
// single-row test cannot rule out a bug that happens to work for the one
// value under test (a memoized "now", an accidental single-shot parse
// outside the map()) -- two rows with two different correct answers can.
const SECOND_ACTIVITY_ITEM = {
    ...BLANK_ROW,
    id: 2,
    timestamp: '2026-06-15T12:15:00Z',
    event_type: 'fault',
    serial_number: 'M2-PROBE',
    location: 'Warehouse',
} satisfies MovementReportItem;

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

/**
 * DATA-M8-2. The reporter, what moved from where to where, and why.
 *
 * The route sent reporter_name as a constant null and the destination as one
 * `location` string; this table declared reporter_name and never showed it.
 * The rows follow the backend's shapes: tests/test_movement_report.py pins
 * which keys each event fills.
 */
describe('DailyActivityTable: reporter, from and to, and reason (DATA-M8-2)', () => {
    const show = async (row: MovementReportItem) => {
        vi.spyOn(api, 'get').mockResolvedValue({ data: [row] });
        render(<DailyActivityTable />);
        return {
            reporter: await screen.findByTestId('reporter'),
            move: screen.getByTestId('move'),
        };
    };

    const HANDOVER = {
        ...BLANK_ROW, id: 10, event_type: 'HANDOVER', serial_number: 'H1',
        reporter_name: 'רס"ן כהן',
        from_holder_name: 'לוי', to_holder_name: 'מזרחי',
        from_owner_name: 'לוי', to_owner_name: 'לוי',
        from_group_name: 'פלוגה א', to_group_name: 'פלוגה א',
    } satisfies MovementReportItem;

    it('shows the reporter', async () => {
        const { reporter } = await show(HANDOVER);
        expect(reporter).toHaveTextContent('רס"ן כהן');
    });

    it('shows a dash for a row with no reporter', async () => {
        const { reporter } = await show({ ...HANDOVER, reporter_name: null });
        expect(reporter).toHaveTextContent(/^—$/);
    });

    it('shows a handover between people as holder to holder, with no unit line inside one unit', async () => {
        const { move } = await show(HANDOVER);
        expect(move).toHaveTextContent(/^לוי ← מזרחי$/);
        expect(screen.queryByTestId('unit')).toBeNull();
        expect(screen.queryByTestId('reason')).toBeNull();
    });

    it('shows a handover to a place as the holder it left to the place', async () => {
        const { move } = await show({
            ...HANDOVER, event_type: 'HANDOVER_LOC', to_holder_name: null, to_location: 'נשקייה',
        });
        expect(move).toHaveTextContent(/^לוי ← נשקייה$/);
    });

    it('shows a handover from a place as the place to the person', async () => {
        const { move } = await show({
            ...HANDOVER, from_holder_name: null, from_location: 'מדף 3',
        });
        expect(move).toHaveTextContent(/^מדף 3 ← מזרחי$/);
    });

    it('shows an assignment as owner to owner, not holder to holder', async () => {
        const { move } = await show({
            ...HANDOVER, event_type: 'ASSIGN',
            from_holder_name: 'מחסנאי', to_holder_name: 'מזרחי',
            from_owner_name: 'לוי', to_owner_name: 'מזרחי',
        });
        expect(move).toHaveTextContent(/^לוי ← מזרחי$/);
    });

    it('shows a dash for the side that was empty', async () => {
        const { move } = await show({
            ...HANDOVER, event_type: 'ASSIGN', from_owner_name: null, to_owner_name: 'מזרחי',
        });
        expect(move).toHaveTextContent(/^— ← מזרחי$/);
    });

    it('adds a unit line only when the unit changed', async () => {
        await show({ ...HANDOVER, to_group_name: 'פלוגה ב' });
        expect(screen.getByTestId('unit')).toHaveTextContent(/^פלוגה א ← פלוגה ב$/);
    });

    it('shows the reason on any event that has one', async () => {
        await show({ ...BLANK_ROW, id: 11, event_type: 'RECLASSIFY', reason: 'הנחיית ביטחון מידע' });
        expect(screen.getByTestId('reason')).toHaveTextContent(/^סיבה: הנחיית ביטחון מידע$/);
        expect(screen.getByTestId('move')).toHaveTextContent(/^—סיבה/);
    });

    it('falls back to the legacy location on a row from before DATA-M8-1', async () => {
        const { move } = await show({ ...BLANK_ROW, id: 12, event_type: 'HANDOVER', location: 'User:Old Name' });
        expect(move).toHaveTextContent(/^User:Old Name$/);
    });

    it('shows a dash for an event that moves nothing', async () => {
        const { move } = await show({ ...BLANK_ROW, id: 13, event_type: 'FAULT', reporter_name: 'לוי' });
        expect(move).toHaveTextContent(/^—$/);
    });

    it('renders a reason and a name as text, never as markup', async () => {
        const hostile = '<img src=x onerror="window.__pwned=1">';
        const { move } = await show({ ...HANDOVER, to_holder_name: hostile, reason: hostile });
        expect(move.querySelector('img')).toBeNull();
        expect(screen.getByTestId('reason')).toHaveTextContent(`סיבה: ${hostile}`);
        expect(move).toHaveTextContent(hostile);
    });

    it('isolates each end, so two Latin names cannot reverse the arrow in the RTL table', async () => {
        const { move } = await show({ ...HANDOVER, from_holder_name: 'Levi', to_holder_name: 'Mizrahi' });
        const ends = move.querySelectorAll('bdi');
        expect(Array.from(ends, (el) => el.textContent)).toEqual(['Levi', 'Mizrahi']);
    });

    it('holds the arrow to the destination, so a narrow cell breaks before the arrow and not after it', async () => {
        // Layout itself is not measurable in jsdom; the live check measured it
        // at 1280px, where the cell is about 100px wide. This pins the
        // structure that layout rests on.
        const { move } = await show(HANDOVER);
        const to = move.querySelectorAll('bdi')[1];
        const held = to.parentElement;
        expect(held?.tagName).toBe('SPAN');
        expect(held).toHaveClass('whitespace-nowrap');
        expect(held?.textContent).toBe('← מזרחי');
    });

    it('keeps a serial on one line in the narrower five-column table', async () => {
        await show(HANDOVER);
        expect(screen.getByText('H1').closest('td')).toHaveClass('whitespace-nowrap');
    });
});

describe('describeMove (DATA-M8-2)', () => {
    it('reads no move off an event outside the three custody events', () => {
        for (const event_type of ['FAULT', 'FIX', 'CREATE', 'VERIFICATION', 'CONDITION_REPORT', 'RECLASSIFY']) {
            expect(describeMove({ ...BLANK_ROW, event_type, to_holder_name: 'x', to_owner_name: 'x' })).toBeNull();
        }
    });

    it('treats a custody row with no destination as legacy', () => {
        expect(describeMove({ ...BLANK_ROW, event_type: 'HANDOVER', from_holder_name: 'לוי' })).toBeNull();
    });
});
