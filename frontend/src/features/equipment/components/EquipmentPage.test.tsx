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
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import EquipmentPage from './EquipmentPage';
import type { Capabilities } from '@/lib/capabilities';
import type { FaultType } from '@/types';
import { TEST_USER, TEST_CAPABILITIES, TEST_CAPABILITIES_NONE, withCapabilities } from '@/test/setup';
import api from '@/lib/axios';

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

function mockEquipmentApi(items: unknown[], faultTypes: FaultType[] = []) {
    vi.spyOn(api, 'get').mockImplementation((url: string) => {
        if (url === '/users/me') return Promise.resolve({ data: USER });
        if (url === '/equipment/accessible') return Promise.resolve({ data: items });
        if (url === '/setup/fault_types') return Promise.resolve({ data: faultTypes });
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

describe('EquipmentPage Report Fault dropdown: the pending filter (API-H6)', () => {
    // The other half of the approval queue. FaultTypeQueuePage tells its user
    // that approving a type adds it to this dropdown; that is true only while
    // the dropdown leaves pending types out and shows approved ones. Nothing
    // pinned the filter before, so deleting it -- or inverting it -- passed.
    const APPROVED = { id: 1, name: 'Cracked Housing', is_pending: false } satisfies FaultType;
    const PENDING = { id: 2, name: 'Frayed Strap', is_pending: true } satisfies FaultType;

    it('offers approved fault types and holds back pending ones', async () => {
        mockEquipmentApi([FUNCTIONAL_ITEM_HELD_BY_USER], [APPROVED, PENDING]);
        render(withCapabilities(<EquipmentPage />, UNGRANTED));

        fireEvent.click(await screen.findByText('דווח תקלה'));

        // The page has two filter selects too; the modal's is the one carrying
        // the free-text escape, which is also how pending types get minted.
        const select = screen.getByText('אחר (חדש)').closest('select');
        if (!select) throw new Error('the Report Fault select did not render');
        const offered = Array.from(select.querySelectorAll('option')).map(o => o.textContent);
        expect(offered).toContain(APPROVED.name);
        expect(offered).not.toContain(PENDING.name);
    });
});
