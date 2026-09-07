/**
 * DATA-H2. The user-visible half of the defect: every ticket card rendered a
 * permanent em-dash where its open date belongs, because `TicketResponse`
 * declared `created_at`/`timestamp` -- two aliases for a column actually named
 * `opened_at` -- and the route passed `opened_at`, which Pydantic v2 discarded
 * as an unknown kwarg. The frontend meanwhile typed `opened_at: string`, a
 * guarantee the API never delivered.
 *
 * `formatDate` returns '—' for null/undefined, so the failure was silent and
 * indistinguishable from "this ticket has no open date" -- which is exactly why
 * it survived to be audited rather than reported. These tests pin both readings
 * apart: a real timestamp must render as a date, and only a genuinely absent
 * one may render the dash.
 *
 * Separate file from MaintenancePage.test.tsx on DATA-H1-3's precedent of one
 * file per ticket. Note the two could share a fixture -- that file's OPEN_TICKET
 * already carries this same opened_at value -- so the split is organisational,
 * not forced: it keeps SEC-H10-3's capability-gating pins and DATA-H2's
 * date-rendering pins failing under their own ticket names.
 *
 * Both dates are asserted through the card body's "נפתח:" label rather than by
 * querying for a bare date string: MaintenancePage renders `formatDate` twice
 * per ticket (the header at :220 and the body at :250), so a bare text query
 * would match twice and `getByText` would throw on the ambiguity.
 *
 * TZ is stubbed to Asia/Kolkata for the same reason DATA-H1-3's tests do it:
 * `toLocaleString('he-IL', ...)` renders in the runtime zone, and asserting a
 * literal wall-clock string under CI's default UTC would silently encode the
 * wrong invariant. Here the zone only has to be FIXED, not non-UTC -- this
 * test is about presence versus absence, not about offset correctness, which
 * DATA-H1-3 already owns.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import MaintenancePage from './MaintenancePage';
import { TEST_CAPABILITIES, withCapabilities } from '@/test/setup';
import api from '@/api';

const BASE_TICKET = {
    id: 1,
    equipment_id: 10,
    equipment_name: 'Rifle',
    fault_type: 'Jammed',
    description: '',
    status: 'Open',
    closed_at: null,
};

function renderTickets(tickets: unknown[]) {
    vi.spyOn(api, 'get').mockImplementation((url: string) => {
        if (url === '/tickets/') return Promise.resolve({ data: tickets });
        throw new Error(`unexpected URL in test: ${url}`);
    });
    return render(withCapabilities(<MaintenancePage />, TEST_CAPABILITIES));
}

afterEach(() => {
    vi.unstubAllEnvs();
});

describe('MaintenancePage renders the open date the API now sends (DATA-H2)', () => {
    it('shows a real date for a ticket carrying opened_at', async () => {
        vi.stubEnv('TZ', 'Asia/Kolkata');

        renderTickets([{ ...BASE_TICKET, opened_at: '2026-01-01T00:00:00Z' }]);

        // 00:00Z under +05:30 is 05:30 on the same day. The assertion is on the
        // rendered string rather than a regex for "any digits", so a future
        // change to formatDate's options is a visible failure, not a silent
        // reformat.
        expect(await screen.findByText('📅 נפתח: 01.01.26, 05:30')).toBeInTheDocument();
    });

    it('does not render the em-dash placeholder when the date is present', async () => {
        vi.stubEnv('TZ', 'Asia/Kolkata');

        renderTickets([{ ...BASE_TICKET, opened_at: '2026-01-01T00:00:00Z' }]);

        // The regression this ticket exists to prevent. Before the backend fix
        // this was the ONLY thing the card could show, for every ticket in the
        // system, forever.
        await screen.findByText('Rifle');
        expect(screen.queryByText('📅 נפתח: —')).toBeNull();
    });

    it('falls back to the em-dash only when opened_at is genuinely null', async () => {
        vi.stubEnv('TZ', 'Asia/Kolkata');

        // Reachable in production: the column is nullable and the model's
        // default is Python-side, so any non-ORM insert lands here. The backend
        // keeps such a ticket in the list rather than failing the whole
        // response (see tests/test_ticket_response_contract.py), which is only
        // worth doing if the client renders it sanely -- this is that half.
        renderTickets([{ ...BASE_TICKET, opened_at: null }]);

        expect(await screen.findByText('📅 נפתח: —')).toBeInTheDocument();
        // The rest of the card must still render -- a missing date degrades one
        // field, not the ticket.
        expect(screen.getByText('Rifle')).toBeInTheDocument();
    });

    it('renders each ticket with its own date, not the first one repeated', async () => {
        vi.stubEnv('TZ', 'Asia/Kolkata');

        // A single-ticket test cannot distinguish a correct per-row read from a
        // value hoisted out of the map. Two rows with two different answers can.
        renderTickets([
            { ...BASE_TICKET, opened_at: '2026-01-01T00:00:00Z' },
            { ...BASE_TICKET, id: 2, equipment_name: 'Radio', opened_at: '2026-03-14T09:00:00Z' },
        ]);

        expect(await screen.findByText('📅 נפתח: 01.01.26, 05:30')).toBeInTheDocument();
        expect(await screen.findByText('📅 נפתח: 14.03.26, 14:30')).toBeInTheDocument();
    });
});
