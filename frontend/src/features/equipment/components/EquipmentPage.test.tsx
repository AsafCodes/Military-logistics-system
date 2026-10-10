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
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { CanceledError } from 'axios';
import EquipmentPage from './EquipmentPage';
import type { Capabilities } from '@/lib/capabilities';
import type { FaultType } from '@/types';
import { TEST_USER, TEST_CAPABILITIES, TEST_CAPABILITIES_NONE, withCapabilities } from '@/test/setup';
import { holdGets } from '@/test/httpStubs';
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

// FE-H5: the page's load passes a signal with every read.
const WITH_SIGNAL = expect.objectContaining({ signal: expect.any(AbortSignal) });

describe('EquipmentPage row actions: cosmetic capability gating (SEC-H10-3)', () => {
    it('offers תקן only with RESOLVE_FAULT', async () => {
        renderWithCapabilities(GRANTED, [MALFUNCTIONING_ITEM]);
        expect(await screen.findByText('תקן')).toBeInTheDocument();
    });

    it('hides תקן without RESOLVE_FAULT', async () => {
        renderWithCapabilities(UNGRANTED, [MALFUNCTIONING_ITEM]);
        await waitFor(() => expect(api.get).toHaveBeenCalledWith('/equipment/accessible', WITH_SIGNAL));
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
        await waitFor(() => expect(api.get).toHaveBeenCalledWith('/equipment/accessible', WITH_SIGNAL));
        expect(screen.queryByText('דווח תקלה')).toBeNull();
    });

    it('offers העבר and שייך only with TRANSFER', async () => {
        renderWithCapabilities(GRANTED, [MALFUNCTIONING_ITEM]);
        expect(await screen.findByText('העבר')).toBeInTheDocument();
        expect(screen.getByText('שייך')).toBeInTheDocument();
    });

    it('hides העבר and שייך without TRANSFER', async () => {
        renderWithCapabilities(UNGRANTED, [MALFUNCTIONING_ITEM]);
        await waitFor(() => expect(api.get).toHaveBeenCalledWith('/equipment/accessible', WITH_SIGNAL));
        expect(screen.queryByText('העבר')).toBeNull();
        expect(screen.queryByText('שייך')).toBeNull();
    });

    it('denies every gated button when rendered outside a CapabilitiesContext provider', async () => {
        // useCapabilities() fail-closes to empty capabilities with no provider
        // (lib/capabilities.ts) -- this is what proves that default actually
        // denies here, not just that it exists.
        renderWithCapabilities(null, [MALFUNCTIONING_ITEM, FUNCTIONAL_ITEM_HELD_BY_OTHER]);
        await waitFor(() => expect(api.get).toHaveBeenCalledWith('/equipment/accessible', WITH_SIGNAL));
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

// The page's own three loads, answered at once; only the searches are held.
const PAGE_LOADS = {
    '/users/me': USER,
    '/equipment/accessible': [MALFUNCTIONING_ITEM],
    '/setup/fault_types': [],
};

/**
 * FE-H5. Both modals' user searches are separate copies of the same debounced
 * effect, so each is tested. In the last test three terms go out, ab → abc →
 * abcd. The newest is settled first; then the oldest RESOLVES late (the
 * modal's post-await check must drop it), and the middle one REJECTS as axios
 * rejects an aborted request (the modal's catch must not report it). Real
 * timers: each term waits out the 300 ms debounce inside waitFor's 1 s.
 */
describe.each([
    { opens: 'העבר', placeholder: 'חפש משתמש...' },
    { opens: 'שייך', placeholder: 'חפש משתמש לשיוך...' },
])('EquipmentPage $opens modal search: a newer term cancels the older request (FE-H5)', ({ opens, placeholder }) => {
    // Opens the modal and types one term; resolves once its request is out.
    async function openAndSearch(term: string) {
        const stub = holdGets(api, PAGE_LOADS);
        render(withCapabilities(<EquipmentPage />, GRANTED));
        fireEvent.click(await screen.findByText(opens));
        fireEvent.change(screen.getByPlaceholderText(placeholder), { target: { value: term } });
        await waitFor(() => expect(stub.held).toHaveLength(1));
        return stub.held;
    }

    it('aborts the search when the modal is closed mid-request', async () => {
        const held = await openAndSearch('ab');
        fireEvent.click(screen.getByText('✕'));
        expect(screen.queryByPlaceholderText(placeholder)).toBeNull();
        expect(held[0].config?.signal?.aborted).toBe(true);
    });

    it('still logs a real failure', async () => {
        const error = vi.spyOn(console, 'error').mockImplementation(() => { });
        const held = await openAndSearch('ab');
        const failure = new Error('timeout of 10000ms exceeded');
        held[0].reject(failure);
        await waitFor(() => expect(error).toHaveBeenCalledWith(failure));
    });

    it("shows only the newest term's results, sent as a parameter, with no error logged", async () => {
        const error = vi.spyOn(console, 'error').mockImplementation(() => { });
        const { held } = holdGets(api, PAGE_LOADS);
        render(withCapabilities(<EquipmentPage />, GRANTED));
        fireEvent.click(await screen.findByText(opens));

        const terms = ['ab', 'abc', 'abcd'];
        for (const [i, term] of terms.entries()) {
            fireEvent.change(screen.getByPlaceholderText(placeholder), { target: { value: term } });
            await waitFor(() => expect(held).toHaveLength(i + 1));
        }
        const [ab, abc, abcd] = held;

        expect(held.map(h => h.url)).toEqual(['/users', '/users', '/users']);
        expect(held.map(h => h.config?.params)).toEqual(terms.map(q => ({ q })));
        expect(held.map(h => h.config?.signal?.aborted)).toEqual([true, true, false]);

        abcd.resolve([{ id: 4, full_name: 'Abcd Newest', personal_number: 'u_abcd' }]);
        expect(await screen.findByText('Abcd Newest')).toBeInTheDocument();

        ab.resolve([{ id: 2, full_name: 'Ab Oldest', personal_number: 'u_ab' }]);
        abc.reject(new CanceledError());
        await act(async () => { });

        expect(screen.queryByText('Ab Oldest')).toBeNull();
        expect(screen.getByText('Abcd Newest')).toBeInTheDocument();
        expect(error).not.toHaveBeenCalled();
    });
});

describe('EquipmentPage transfer modal: switching to a location mid-search (FE-H5)', () => {
    it('aborts the person search, and switching back searches the kept term again', async () => {
        const { held } = holdGets(api, PAGE_LOADS);
        render(withCapabilities(<EquipmentPage />, GRANTED));
        fireEvent.click(await screen.findByText('העבר'));
        fireEvent.change(screen.getByPlaceholderText('חפש משתמש...'), { target: { value: 'ab' } });
        await waitFor(() => expect(held).toHaveLength(1));

        fireEvent.click(screen.getByText('📍 העבר למיקום'));
        expect(held[0].config?.signal?.aborted).toBe(true);
        // A response that still lands must not fill the person list, which
        // would show again, with stale entries, on switching back.
        held[0].resolve([{ id: 2, full_name: 'Stale Person', personal_number: 'u_stale' }]);
        await act(async () => { });

        fireEvent.click(screen.getByText('👤 העבר לאדם'));
        await waitFor(() => expect(held).toHaveLength(2));
        expect(held[1].config?.params).toEqual({ q: 'ab' });
        expect(screen.queryByText('Stale Person')).toBeNull();
    });
});

// The expand toggle is an icon-only button; its chevron is the handle.
function expandToggle(container: HTMLElement) {
    const button = container.querySelector('.lucide-chevron-down, .lucide-chevron-up')?.closest('button');
    if (!button) throw new Error('the expand toggle did not render');
    return button;
}

describe('EquipmentPage inline history: collapsing the row aborts its read (FE-H5)', () => {
    it('aborts the history request and logs nothing when it is cancelled', async () => {
        const error = vi.spyOn(console, 'error').mockImplementation(() => { });
        const { held } = holdGets(api, PAGE_LOADS);
        const { container } = render(withCapabilities(<EquipmentPage />, GRANTED));
        await screen.findByText('העבר');

        fireEvent.click(expandToggle(container));
        await waitFor(() => expect(held.map(h => h.url)).toEqual([`/equipment/${MALFUNCTIONING_ITEM.id}/history`]));
        expect(screen.getByText('טוען היסטוריה...')).toBeInTheDocument();
        expect(held[0].config?.signal?.aborted).toBe(false);

        fireEvent.click(expandToggle(container));
        expect(screen.queryByText('טוען היסטוריה...')).toBeNull();
        expect(held[0].config?.signal?.aborted).toBe(true);

        held[0].reject(new CanceledError());
        await act(async () => { });
        expect(error).not.toHaveBeenCalled();
    });

    it('shows the history once it arrives', async () => {
        const { held } = holdGets(api, PAGE_LOADS);
        const { container } = render(withCapabilities(<EquipmentPage />, GRANTED));
        await screen.findByText('העבר');

        fireEvent.click(expandToggle(container));
        await waitFor(() => expect(held).toHaveLength(1));
        held[0].resolve([{
            id: 1, old_status: 'Old-Probe', new_status: 'New-Probe',
            change_reason: 'fault', created_date: '2026-06-15T09:30:00Z', user_name: null,
        }]);

        expect(await screen.findByText('New-Probe')).toBeInTheDocument();
        expect(screen.queryByText('טוען היסטוריה...')).toBeNull();
    });

    it('still logs a real failure and stops loading', async () => {
        const error = vi.spyOn(console, 'error').mockImplementation(() => { });
        const { held } = holdGets(api, PAGE_LOADS);
        const { container } = render(withCapabilities(<EquipmentPage />, GRANTED));
        await screen.findByText('העבר');

        fireEvent.click(expandToggle(container));
        await waitFor(() => expect(held).toHaveLength(1));
        const failure = new Error('timeout of 10000ms exceeded');
        held[0].reject(failure);

        expect(await screen.findByText('אין רשומות היסטוריה.')).toBeInTheDocument();
        expect(error).toHaveBeenCalledWith(failure);
    });
});
