/**
 * SEC-H10-3. The close-ticket button's own comment says "the backend's
 * RESOLVE_FAULT gate is what actually decides this; nothing here does" --
 * this test pins the cosmetic narrowing added to match. This button posts
 * POST /maintenance/tickets/{id}/close and EquipmentPage's תקן posts
 * POST /maintenance/fix/{id} (DATA-M21); both routes gate on RESOLVE_FAULT,
 * so the same `hasAnywhere(caps, RESOLVE_FAULT)` check applies here.
 */
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import MaintenancePage from './MaintenancePage';
import type { Capabilities } from '@/lib/capabilities';
import { TEST_CAPABILITIES, TEST_CAPABILITIES_NONE, withCapabilities } from '@/test/setup';
import api from '@/lib/axios';

const OPEN_TICKET = {
    id: 1, equipment_id: 10, equipment_name: 'Rifle', fault_type: 'Jammed',
    description: '', status: 'Open', opened_at: '2026-01-01T00:00:00Z',
};

function renderWithCapabilities(caps: Capabilities) {
    vi.spyOn(api, 'get').mockImplementation((url: string) => {
        if (url === '/tickets/') return Promise.resolve({ data: [OPEN_TICKET] });
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
