/**
 * SEC-H10-3. The ticket footer's own comment says the backend's
 * RESOLVE_FAULT gate is what actually decides this, and the footer is only
 * cosmetically narrowed to match -- this test pins that narrowing. Its
 * buttons post POST /maintenance/tickets/{id}/close, /start and /release
 * (DATA-M21, DATA-M22), and EquipmentPage's תקן posts
 * POST /maintenance/fix/{id}; all four routes gate on RESOLVE_FAULT, so the
 * same `hasAnywhere(caps, RESOLVE_FAULT)` check applies here.
 */
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import MaintenancePage from './MaintenancePage';
import type { Capabilities } from '@/lib/capabilities';
import { TEST_CAPABILITIES, TEST_CAPABILITIES_NONE, withCapabilities } from '@/test/setup';
import api from '@/lib/axios';
import { holdPosts } from '@/test/httpStubs';

const OPEN_TICKET = {
    id: 1, equipment_id: 10, equipment_name: 'Rifle', fault_type: 'Jammed',
    description: '', status: 'Open', opened_at: '2026-01-01T00:00:00Z',
};

function renderWithCapabilities(caps: Capabilities, tickets: object[] = [OPEN_TICKET]) {
    vi.spyOn(api, 'get').mockImplementation((url: string) => {
        if (url === '/tickets/') return Promise.resolve({ data: tickets });
        throw new Error(`unexpected URL in test: ${url}`);
    });
    return render(withCapabilities(<MaintenancePage />, caps));
}

describe('MaintenancePage close-ticket button: cosmetic capability gating (SEC-H10-3)', () => {
    it('offers סגור כרטיס with RESOLVE_FAULT', async () => {
        // TEST_CAPABILITIES (the master fixture) holds RESOLVE_FAULT among
        // everything else -- sufficient here, no second literal needed.
        renderWithCapabilities(TEST_CAPABILITIES);
        expect(await screen.findByText(/סגור כרטיס/)).toBeInTheDocument();
    });

    it('hides סגור כרטיס without RESOLVE_FAULT', async () => {
        renderWithCapabilities(TEST_CAPABILITIES_NONE);
        await waitFor(() => expect(api.get).toHaveBeenCalledWith(
            '/tickets/', expect.objectContaining({ signal: expect.any(AbortSignal) })));
        // The ticket itself still renders (it's a read) -- only the write
        // action is gated.
        expect(await screen.findByText('Rifle')).toBeInTheDocument();
        expect(screen.queryByText(/סגור כרטיס/)).toBeNull();
    });
});

describe('MaintenancePage closes a ticket by its own id (DATA-M21)', () => {
    it('posts the ticket route, not the item route', async () => {
        // id and equipment_id differ, so a URL built from the wrong one fails.
        const post = vi.spyOn(api, 'post').mockResolvedValue({ data: {} });
        renderWithCapabilities(TEST_CAPABILITIES);
        fireEvent.click(await screen.findByText(/סגור כרטיס/));
        await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
        expect(post).toHaveBeenCalledWith('/maintenance/tickets/1/close');
    });
});

// DATA-M22. A ticket can be taken (start) and put back (release).
const TAKEN_TICKET = {
    id: 2, equipment_id: 20, equipment_name: 'Radio', fault_type: 'Dead Battery',
    description: '', status: 'In Progress', opened_at: '2026-01-01T00:00:00Z',
    assigned_to_name: 'Tech Co A', started_at: '2026-01-02T08:30:00Z',
};
const WAITING_TICKET = {
    id: 3, equipment_id: 30, equipment_name: 'Scope', fault_type: 'Cracked Lens',
    description: '', status: 'Waiting for Parts', opened_at: '2026-01-01T00:00:00Z',
};

/** The card that shows `equipmentName`. */
function cardOf(equipmentName: string): HTMLElement {
    const card = screen.getByText(equipmentName).closest('.glass-card');
    if (!(card instanceof HTMLElement)) throw new Error(`no card for ${equipmentName}`);
    return card;
}

function buttonsOf(equipmentName: string): string[] {
    return Array.from(cardOf(equipmentName).querySelectorAll('button')).map(b => b.textContent?.trim() ?? '');
}

describe('MaintenancePage takes and releases a ticket (DATA-M22)', () => {
    it('offers take and close on an Open ticket, release and close on an In Progress one', async () => {
        renderWithCapabilities(TEST_CAPABILITIES, [OPEN_TICKET, TAKEN_TICKET, WAITING_TICKET]);
        await screen.findByText('Rifle');

        expect(buttonsOf('Rifle')).toEqual(['קח לטיפול', 'סגור כרטיס']);
        expect(buttonsOf('Radio')).toEqual(['שחרר', 'סגור כרטיס']);
        // Nothing moves a ticket out of Waiting for Parts but a close.
        expect(buttonsOf('Scope')).toEqual(['סגור כרטיס']);
    });

    it('offers neither without RESOLVE_FAULT', async () => {
        renderWithCapabilities(TEST_CAPABILITIES_NONE, [OPEN_TICKET, TAKEN_TICKET]);
        await screen.findByText('Rifle');

        expect(screen.queryByText('קח לטיפול')).toBeNull();
        expect(screen.queryByText('שחרר')).toBeNull();
    });

    it.each([
        ['קח לטיפול', 'Rifle', '/maintenance/tickets/1/start'],
        ['שחרר', 'Radio', '/maintenance/tickets/2/release'],
    ])('%s posts the ticket route', async (label, equipmentName, url) => {
        const post = vi.spyOn(api, 'post').mockResolvedValue({ data: {} });
        renderWithCapabilities(TEST_CAPABILITIES, [OPEN_TICKET, TAKEN_TICKET]);
        await screen.findByText(equipmentName);

        fireEvent.click(screen.getByText(label));

        await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
        expect(post).toHaveBeenCalledWith(url);
    });

    it('shows who has an In Progress ticket, and on no other', async () => {
        renderWithCapabilities(TEST_CAPABILITIES_NONE, [OPEN_TICKET, TAKEN_TICKET]);
        await screen.findByText('Radio');

        expect(cardOf('Radio').textContent).toContain('בטיפול אצל Tech Co A');
        expect(cardOf('Rifle').textContent).not.toContain('בטיפול אצל');
    });

    it('lists an In Progress ticket under the בטיפול tab', async () => {
        renderWithCapabilities(TEST_CAPABILITIES_NONE, [OPEN_TICKET, TAKEN_TICKET]);
        await screen.findByText('Radio');

        fireEvent.click(screen.getByText('בטיפול (1)'));

        expect(screen.getByText('Radio')).toBeInTheDocument();
        expect(screen.queryByText('Rifle')).toBeNull();
    });

    it('a failed take is one alert naming the take', async () => {
        vi.spyOn(console, 'error').mockImplementation(() => { });
        const alert = vi.spyOn(window, 'alert').mockImplementation(() => { });
        vi.spyOn(api, 'post').mockRejectedValue(new Error('409'));
        renderWithCapabilities(TEST_CAPABILITIES, [OPEN_TICKET]);
        await screen.findByText('Rifle');

        fireEvent.click(screen.getByText('קח לטיפול'));

        await waitFor(() => expect(alert).toHaveBeenCalledTimes(1));
        expect(alert).toHaveBeenCalledWith('לקיחת הכרטיס לטיפול נכשלה');
        await waitFor(() => expect(screen.getByText('קח לטיפול').closest('button')).toBeEnabled());
    });

    it('while a take is out, that ticket waits and another ticket does not', async () => {
        const posts = holdPosts(api);
        renderWithCapabilities(TEST_CAPABILITIES, [OPEN_TICKET, TAKEN_TICKET]);
        await screen.findByText('Rifle');

        fireEvent.click(screen.getByText('קח לטיפול'));

        expect(posts.map(p => p.url)).toEqual(['/maintenance/tickets/1/start']);
        // The spinner is on the button pressed, and its neighbour waits too.
        expect(buttonsOf('Rifle')).toEqual(['לוקח...', 'סגור כרטיס']);
        for (const button of cardOf('Rifle').querySelectorAll('button')) {
            expect(button).toBeDisabled();
        }
        for (const button of cardOf('Radio').querySelectorAll('button')) {
            expect(button).toBeEnabled();
        }
    });

    it('reloads the list once a take succeeds', async () => {
        vi.spyOn(api, 'post').mockResolvedValue({ data: {} });
        renderWithCapabilities(TEST_CAPABILITIES, [OPEN_TICKET]);
        await screen.findByText('Rifle');
        expect(api.get).toHaveBeenCalledTimes(1);

        fireEvent.click(screen.getByText('קח לטיפול'));

        await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
    });

    it('a taken ticket whose assignee has no name still says it is taken', async () => {
        renderWithCapabilities(TEST_CAPABILITIES_NONE, [{ ...TAKEN_TICKET, assigned_to_name: null }]);
        await screen.findByText('Radio');

        expect(cardOf('Radio').textContent).toContain('בטיפול אצל —');
    });
});

// DATA-M22-2. Every take is kept, and the card lists the ended ones.
const CLAIMED_TICKET = {
    ...TAKEN_TICKET,
    assigned_to_name: 'Dana',
    claims: [
        // Newest first, as the API sends them; this one is still open.
        { id: 13, user_id: 7, user_name: 'Dana', started_at: '2026-01-04T08:00:00Z' },
        {
            id: 12, user_id: 5, user_name: 'יוסי', started_at: '2026-01-03T08:00:00Z',
            ended_at: '2026-01-03T12:30:00Z', ended_by_id: 6, ended_by_name: 'רון', outcome: 'Released',
        },
        {
            id: 11, user_id: 6, user_name: 'רון', started_at: '2026-01-02T08:00:00Z',
            ended_at: '2026-01-02T15:00:00Z', ended_by_id: 6, ended_by_name: 'רון', outcome: 'Released',
        },
    ],
};

function historyOf(equipmentName: string): string[] {
    return Array.from(cardOf(equipmentName).querySelectorAll('li')).map(li => li.textContent ?? '');
}

describe('MaintenancePage shows who held a ticket (DATA-M22-2)', () => {
    it('lists the ended takes newest first, and not the open one', async () => {
        renderWithCapabilities(TEST_CAPABILITIES_NONE, [CLAIMED_TICKET]);
        await screen.findByText('Radio');

        expect(cardOf('Radio').textContent).toContain('היסטוריית טיפול:');
        const lines = historyOf('Radio');
        expect(lines).toHaveLength(2);
        expect(lines[0]).toMatch(/^יוסי · .+ – .+ · שוחרר ע״י רון$/);
        expect(lines[1]).toMatch(/^רון · .+ – .+ · שוחרר$/);
        // The open take is the בטיפול אצל line, once.
        expect(cardOf('Radio').textContent?.match(/Dana/g)).toHaveLength(1);
    });

    it('isolates each name and date in its own <bdi>', async () => {
        renderWithCapabilities(TEST_CAPABILITIES_NONE, [CLAIMED_TICKET]);
        await screen.findByText('Radio');

        const items = Array.from(cardOf('Radio').querySelectorAll('li'));
        const isolated = items.map(li => Array.from(li.querySelectorAll('bdi')).map(b => b.textContent));
        // Taker, start, end, and the ender only where someone else ended it.
        expect(isolated).toEqual([
            ['יוסי', expect.any(String), expect.any(String), 'רון'],
            ['רון', expect.any(String), expect.any(String)],
        ]);
        for (const [, started, ended] of isolated) {
            expect(started).toMatch(/\d/);
            expect(ended).toMatch(/\d/);
        }
    });

    it('says a take ended with the close', async () => {
        renderWithCapabilities(TEST_CAPABILITIES_NONE, [{
            ...OPEN_TICKET, status: 'Closed', closed_at: '2026-01-02T15:00:00Z',
            claims: [{
                id: 11, user_id: 5, user_name: 'Tech Co A', started_at: '2026-01-02T08:00:00Z',
                ended_at: '2026-01-02T15:00:00Z', ended_by_id: 6, ended_by_name: 'Master Admin', outcome: 'Closed',
            }],
        }]);
        await screen.findByText('Rifle');

        expect(historyOf('Rifle')).toEqual([
            expect.stringMatching(/^Tech Co A · .+ – .+ · נסגר ע״י Master Admin$/),
        ]);
    });

    it('shows no history for a ticket whose only take is open, or that was never taken', async () => {
        renderWithCapabilities(TEST_CAPABILITIES_NONE, [
            { ...OPEN_TICKET, claims: [] },
            { ...TAKEN_TICKET, claims: [{ id: 1, user_id: 5, user_name: 'Tech Co A', started_at: '2026-01-02T08:30:00Z' }] },
        ]);
        await screen.findByText('Radio');

        for (const name of ['Rifle', 'Radio']) {
            expect(cardOf(name).textContent).not.toContain('היסטוריית טיפול');
            expect(historyOf(name)).toEqual([]);
        }
    });

    it('a take by or ended by someone with no name still shows', async () => {
        renderWithCapabilities(TEST_CAPABILITIES_NONE, [{
            ...OPEN_TICKET,
            claims: [{
                id: 11, user_id: 5, user_name: null, started_at: '2026-01-02T08:00:00Z',
                ended_at: '2026-01-02T15:00:00Z', ended_by_id: 6, ended_by_name: null, outcome: 'Released',
            }],
        }]);
        await screen.findByText('Rifle');

        expect(historyOf('Rifle')).toEqual([expect.stringMatching(/^— · .+ – .+ · שוחרר ע״י —$/)]);
    });
});
