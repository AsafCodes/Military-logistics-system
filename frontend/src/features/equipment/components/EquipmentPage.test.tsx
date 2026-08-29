/**
 * SEC-H10-3. EquipmentPage's action buttons carry comments naming a real
 * backend gate for each ("RESOLVE_FAULT is the backend's real gate, not
 * this button") -- meaning the button itself checked nothing at all. These
 * tests pin the client-side approximation added here: `anywhere` over-shows
 * (holds the verb over SOME group, never necessarily this item's), so it can
 * only hide a button the backend would have refused anyway, never the
 * reverse -- see lib/capabilities.ts and EquipmentPage.tsx's own comments.
 *
 * EquipmentRow is module-local (not exported), so this renders the whole
 * page rather than the row directly: exporting a component purely to make
 * it testable would be a production change with no runtime caller, and
 * going through the page also proves the CapabilitiesContext provider
 * genuinely reaches the row rather than assuming it does.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import EquipmentPage from './EquipmentPage';
import type { Capabilities } from '@/lib/capabilities';
import { TEST_USER, TEST_CAPABILITIES, TEST_CAPABILITIES_NONE, withCapabilities } from '@/test/setup';
import api from '@/api';

// TEST_USER's shape, not its identity content -- 'Master Admin' reads oddly
// as the persona for a deliberately ungranted-soldier scenario, so the
// fields that matter to THESE tests are overridden while everything else
// still comes from the one place the User shape is owned.
const USER = { ...TEST_USER, id: 1, personal_number: 'u_test', full_name: 'Test User' };

const MALFUNCTIONING_ITEM = {
    id: 10, type: 'Rifle', item_name: 'Rifle', status: 'Malfunctioning',
    current_state_description: '', compliance_check: '', report_status: '',
    compliance_level: 'NEUTRAL', holder_user_id: 2, serial_number: 'M1',
};

const FUNCTIONAL_ITEM_HELD_BY_USER = {
    id: 20, type: 'Vest', item_name: 'Vest', status: 'Functional',
    current_state_description: '', compliance_check: '', report_status: '',
    compliance_level: 'NEUTRAL', holder_user_id: USER.id, serial_number: 'F1',
};

const FUNCTIONAL_ITEM_HELD_BY_OTHER = {
    ...FUNCTIONAL_ITEM_HELD_BY_USER, id: 21, holder_user_id: 999, serial_number: 'F2',
};

function mockEquipmentApi(items: unknown[]) {
    vi.spyOn(api, 'get').mockImplementation((url: string) => {
        if (url === '/users/me') return Promise.resolve({ data: USER });
        if (url === '/equipment/accessible') return Promise.resolve({ data: items });
        if (url === '/setup/fault_types') return Promise.resolve({ data: [] });
        throw new Error(`unexpected URL in test: ${url}`);
    });
}

function renderWithCapabilities(caps: Capabilities | null, items: unknown[]) {
    mockEquipmentApi(items);
    return render(withCapabilities(<EquipmentPage />, caps));
}

// TEST_CAPABILITIES is the master fixture (holds everything, per
// tests/test_capabilities_endpoint.py's EXPECTED table) -- exactly what
// GRANTED needs to mean here too, so no second literal is written for it.
const GRANTED = TEST_CAPABILITIES;
const UNGRANTED = TEST_CAPABILITIES_NONE;

describe('EquipmentPage row actions: cosmetic capability gating (SEC-H10-3)', () => {
    it('offers תקן only with RESOLVE_FAULT', async () => {
        renderWithCapabilities(GRANTED, [MALFUNCTIONING_ITEM]);
        expect(await screen.findByText('תקן')).toBeInTheDocument();
    });

    it('hides תקן without RESOLVE_FAULT', async () => {
        renderWithCapabilities(UNGRANTED, [MALFUNCTIONING_ITEM]);
        await waitFor(() => expect(api.get).toHaveBeenCalledWith('/equipment/accessible'));
        expect(screen.queryByText('תקן')).toBeNull();
    });

    it('offers דווח תקלה to the holder even without REPORT_STATUS -- the OR', async () => {
        renderWithCapabilities(UNGRANTED, [FUNCTIONAL_ITEM_HELD_BY_USER]);
        expect(await screen.findByText('דווח תקלה')).toBeInTheDocument();
    });

    it('offers דווח תקלה to a non-holder who has REPORT_STATUS', async () => {
        renderWithCapabilities(GRANTED, [FUNCTIONAL_ITEM_HELD_BY_OTHER]);
        expect(await screen.findByText('דווח תקלה')).toBeInTheDocument();
    });

    it('hides דווח תקלה from a non-holder without REPORT_STATUS', async () => {
        renderWithCapabilities(UNGRANTED, [FUNCTIONAL_ITEM_HELD_BY_OTHER]);
        await waitFor(() => expect(api.get).toHaveBeenCalledWith('/equipment/accessible'));
        expect(screen.queryByText('דווח תקלה')).toBeNull();
    });

    it('offers העבר and שייך only with TRANSFER', async () => {
        renderWithCapabilities(GRANTED, [MALFUNCTIONING_ITEM]);
        expect(await screen.findByText('העבר')).toBeInTheDocument();
        expect(screen.getByText('שייך')).toBeInTheDocument();
    });

    it('hides העבר and שייך without TRANSFER', async () => {
        renderWithCapabilities(UNGRANTED, [MALFUNCTIONING_ITEM]);
        await waitFor(() => expect(api.get).toHaveBeenCalledWith('/equipment/accessible'));
        expect(screen.queryByText('העבר')).toBeNull();
        expect(screen.queryByText('שייך')).toBeNull();
    });

    it('denies every gated button when rendered outside a CapabilitiesContext provider', async () => {
        // useCapabilities() fail-closes to empty capabilities with no provider
        // (lib/capabilities.ts) -- this is what proves that default actually
        // denies here, not just that it exists.
        renderWithCapabilities(null, [MALFUNCTIONING_ITEM, FUNCTIONAL_ITEM_HELD_BY_OTHER]);
        await waitFor(() => expect(api.get).toHaveBeenCalledWith('/equipment/accessible'));
        expect(screen.queryByText('תקן')).toBeNull();
        expect(screen.queryByText('דווח תקלה')).toBeNull();
        expect(screen.queryByText('העבר')).toBeNull();
        expect(screen.queryByText('שייך')).toBeNull();
    });
});
