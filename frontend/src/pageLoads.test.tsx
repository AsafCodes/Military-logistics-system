/**
 * FE-H6-3. The five page loads no longer switch their own loading state on:
 * the flag starts true for the first load, and every later load goes through
 * a `reload` that sets it. These tests pin what that split could break: a
 * page shows loading on its first frame, a caller that starts a later load
 * shows loading again, and a load that fails still ends it.
 *
 * Two cases live in the pages' own test files: FaultTypeQueuePage's first
 * frame, and GeneralReportPage's Refresh, which its "Refresh twice" tests
 * cover.
 *
 * Lint cannot stand in for these. react-hooks/set-state-in-effect looks only
 * at calls written directly in an effect, so it says nothing about what a
 * load does before its first await.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, fireEvent, within, act } from '@testing-library/react';
import { renderToStaticMarkup } from 'react-dom/server';
import FaultTypeQueuePage from '@/features/catalog/components/FaultTypeQueuePage';
import AdminPanel from '@/features/dashboard/components/AdminPanel';
import EquipmentPage from '@/features/equipment/components/EquipmentPage';
import MaintenancePage from '@/features/maintenance/components/MaintenancePage';
import GeneralReportPage from '@/features/reports/components/GeneralReportPage';
import api from '@/lib/axios';
import { holdGets, holdPosts, type HeldGet } from '@/test/httpStubs';
import { TEST_USER, TEST_CAPABILITIES, withCapabilities } from '@/test/setup';
import type { FaultType } from '@/types';

const spinnerIn = (container: HTMLElement) => container.querySelector('.animate-spin');
const quietConsole = () => vi.spyOn(console, 'error').mockImplementation(() => { });

// Server rendering runs no effects, so it sends no request and its markup is
// the first frame: what the initial state shows. Only the report page's
// initial value changed here. The other two already started true, and since
// the loads stopped setting the flag, that value is all that shows loading
// during a first load.
describe('first frame: loading, not an empty page (FE-H6-3)', () => {
    it('the maintenance page', () => {
        const frame = renderToStaticMarkup(withCapabilities(<MaintenancePage />, TEST_CAPABILITIES));
        expect(frame).toContain('animate-spin');
        expect(frame).not.toContain('תחזוקה וכרטיסים');
    });

    it('the equipment page', () => {
        const frame = renderToStaticMarkup(withCapabilities(<EquipmentPage />, TEST_CAPABILITIES));
        expect(frame).toContain('animate-spin');
        expect(frame).not.toContain('ניהול ציוד');
    });

    it('the report page, which used to paint an empty table first', () => {
        const frame = renderToStaticMarkup(<GeneralReportPage />);
        expect(frame).toContain('animate-spin');
        expect(frame).not.toContain('<table');
    });
});

describe('FaultTypeQueuePage: Retry (FE-H6-3)', () => {
    const LOADING = 'טוען...';
    const FAILED = /טעינת התור נכשלה/;

    it('shows loading again while the retry is out, and a second failure ends it', async () => {
        quietConsole();
        const { held } = holdGets(api);
        render(<FaultTypeQueuePage />);
        await waitFor(() => expect(held).toHaveLength(1));

        await act(async () => { held[0].reject(new Error('network down')); });
        expect(screen.getByText(FAILED)).toBeInTheDocument();
        expect(screen.queryByText(LOADING)).toBeNull();

        fireEvent.click(screen.getByText('נסה שוב'));
        expect(held).toHaveLength(2);
        expect(screen.getByText(LOADING)).toBeInTheDocument();
        expect(screen.queryByText(FAILED)).toBeNull();

        await act(async () => { held[1].reject(new Error('still down')); });
        expect(screen.queryByText(LOADING)).toBeNull();
        expect(screen.getByText(FAILED)).toBeInTheDocument();
    });
});

describe('AdminPanel groups: Retry (FE-H6-3)', () => {
    const LOADING = 'טוען קבוצות...';
    const FAILED = /טעינת רשימת הקבוצות נכשלה/;
    const USER = { id: 1, full_name: 'Test User', personal_number: 'u_test' };

    it('shows loading again while the retry is out, and a second failure ends it', async () => {
        quietConsole();
        const { held } = holdGets(api, { '/users': [USER] });
        render(<AdminPanel />);
        await waitFor(() => expect(held).toHaveLength(1));
        // The group selector renders only once a user is selected.
        fireEvent.change(screen.getByPlaceholderText('התחל להקליד...'), { target: { value: 'Test' } });
        fireEvent.click(await screen.findByText(USER.full_name));
        expect(screen.getByText(LOADING)).toBeInTheDocument();

        await act(async () => { held[0].reject(new Error('network down')); });
        expect(screen.getByText(FAILED)).toBeInTheDocument();
        expect(screen.queryByText(LOADING)).toBeNull();

        fireEvent.click(screen.getByText('נסה שוב'));
        expect(held.map(h => h.url)).toEqual(['/groups', '/groups']);
        expect(screen.getByText(LOADING)).toBeInTheDocument();
        expect(screen.queryByText(FAILED)).toBeNull();

        await act(async () => { held[1].reject(new Error('still down')); });
        expect(screen.queryByText(LOADING)).toBeNull();
        expect(screen.getByText(FAILED)).toBeInTheDocument();
    });
});

describe('MaintenancePage: loads after the first (FE-H6-3)', () => {
    const TITLE = 'תחזוקה וכרטיסים';
    const LOAD_FAILED = 'טעינת כרטיסי תחזוקה נכשלה';
    const CLOSE = /סגור כרטיס/;
    const TICKET = {
        id: 1, equipment_id: 10, equipment_name: 'Rifle', fault_type: 'Jammed',
        description: '', status: 'Open', opened_at: '2026-01-01T00:00:00Z',
    };

    async function mountWithOneTicket() {
        const error = quietConsole();
        const posts = holdPosts(api);
        const { held } = holdGets(api);
        const { container } = render(withCapabilities(<MaintenancePage />, TEST_CAPABILITIES));
        await waitFor(() => expect(held).toHaveLength(1));
        await act(async () => { held[0].resolve([TICKET]); });
        expect(screen.getByText(TITLE)).toBeInTheDocument();
        return { error, posts, held, spinner: () => spinnerIn(container) };
    }

    it('a first load that fails ends loading and shows the failure', async () => {
        quietConsole();
        const { held } = holdGets(api);
        const { container } = render(withCapabilities(<MaintenancePage />, TEST_CAPABILITIES));
        await waitFor(() => expect(held).toHaveLength(1));
        expect(spinnerIn(container)).not.toBeNull();

        await act(async () => { held[0].reject(new Error('network down')); });

        expect(spinnerIn(container)).toBeNull();
        expect(screen.getByText(LOAD_FAILED)).toBeInTheDocument();
    });

    it('Refresh shows loading again, and a failed refresh ends it', async () => {
        const { held, spinner } = await mountWithOneTicket();

        fireEvent.click(screen.getByText('רענן'));
        expect(held).toHaveLength(2);
        expect(spinner()).not.toBeNull();
        expect(screen.queryByText(TITLE)).toBeNull();

        await act(async () => { held[1].reject(new Error('network down')); });
        expect(spinner()).toBeNull();
        expect(screen.getByText(LOAD_FAILED)).toBeInTheDocument();
    });

    it('closing a ticket shows loading while its reload is out', async () => {
        const { posts, held, spinner } = await mountWithOneTicket();

        fireEvent.click(screen.getByText(CLOSE));
        expect(posts.map(p => p.url)).toEqual(['/maintenance/fix/10']);
        await act(async () => { posts[0].resolve(); });

        expect(held).toHaveLength(2);
        expect(spinner()).not.toBeNull();
        expect(screen.queryByText(TITLE)).toBeNull();

        // A ticket the reload still lists as open can be closed again.
        await act(async () => { held[1].resolve([TICKET]); });
        expect(spinner()).toBeNull();
        expect(screen.getByText(TITLE)).toBeInTheDocument();
        expect(screen.getByText(CLOSE).closest('button')).toBeEnabled();
    });

    it('a failed close says so, reloads nothing and can be pressed again', async () => {
        const { error, posts, held, spinner } = await mountWithOneTicket();
        const alert = vi.spyOn(window, 'alert').mockImplementation(() => { });

        fireEvent.click(screen.getByText(CLOSE));
        // The button is busy while the write is out.
        expect(screen.getByText('סוגר...').closest('button')).toBeDisabled();

        const failure = new Error('timeout of 10000ms exceeded');
        await act(async () => { posts[0].reject(failure); });

        expect(alert.mock.calls).toEqual([['סגירת הכרטיס נכשלה']]);
        expect(error).toHaveBeenCalledWith(failure);
        expect(held).toHaveLength(1);
        expect(spinner()).toBeNull();
        expect(screen.getByText(CLOSE).closest('button')).toBeEnabled();

        fireEvent.click(screen.getByText(CLOSE));
        expect(posts).toHaveLength(2);
    });
});

describe('GeneralReportPage: a first load that fails (FE-H6-3)', () => {
    it('ends loading and shows the table', async () => {
        const error = quietConsole();
        const { held } = holdGets(api);
        const { container } = render(<GeneralReportPage />);
        await waitFor(() => expect(held).toHaveLength(1));
        expect(spinnerIn(container)).not.toBeNull();

        const failure = new Error('network down');
        await act(async () => { held[0].reject(failure); });

        expect(spinnerIn(container)).toBeNull();
        expect(container.querySelector('table')).not.toBeNull();
        expect(error).toHaveBeenCalledWith('Failed to load report', failure);
    });
});

describe('EquipmentPage: loads after the first (FE-H6-3)', () => {
    const TITLE = 'ניהול ציוד';
    const LOAD_FAILED = 'טעינת נתוני ציוד נכשלה';
    const PAGE_URLS = ['/users/me', '/equipment/accessible', '/setup/fault_types'];

    // Held by the session user, functional and at compliance level WARNING,
    // so its row offers every write but the repair.
    const VEST = {
        id: 20, type: 'Vest', item_name: 'Vest', status: 'Functional',
        current_state_description: '', compliance_check: '', report_status: '',
        compliance_level: 'WARNING', holder_user_id: TEST_USER.id, serial_number: 'F1',
    };
    const RIFLE = {
        ...VEST, id: 10, type: 'Rifle', item_name: 'Rifle', status: 'Malfunctioning',
        compliance_level: 'NEUTRAL', holder_user_id: 999, serial_number: 'M1',
    };
    const LISTED = { id: 3, name: 'Cracked Housing', is_pending: false } satisfies FaultType;
    const PERSON = { id: 4, full_name: 'Dana Levi', personal_number: 'u_dana' };
    const PAGE_DATA: Record<string, unknown> = {
        '/users/me': TEST_USER,
        '/equipment/accessible': [VEST, RIFLE],
        '/setup/fault_types': [LISTED],
    };

    /**
     * Mounts the page with its first load answered. After that every page
     * read is held, so a reload stays out until the test answers it; the
     * modals' user search is still answered at once.
     */
    async function mountLoaded() {
        const error = quietConsole();
        const answers: Record<string, unknown> = { ...PAGE_DATA, '/users': [PERSON] };
        const { held } = holdGets(api, answers);
        const posts = holdPosts(api);
        const { container } = render(withCapabilities(<EquipmentPage />, TEST_CAPABILITIES));
        // Three seconds, as in App.test.tsx: the file's first render of the
        // page also pays for loading the modules.
        await screen.findByText(TITLE, {}, { timeout: 3000 });
        for (const url of PAGE_URLS) delete answers[url];
        return { error, held, posts, spinner: () => spinnerIn(container) };
    }

    /** The modal opened by a row button, found by its title. */
    function modalTitled(title: string) {
        const card = screen.getByText(title).closest('.glass-card');
        if (!(card instanceof HTMLElement)) throw new Error('the modal did not render');
        return within(card);
    }

    async function pickPerson(modal: ReturnType<typeof within>, placeholder: string) {
        fireEvent.change(modal.getByPlaceholderText(placeholder), { target: { value: 'da' } });
        fireEvent.click(await modal.findByText(PERSON.full_name));
    }

    function expectReloadOut(held: HeldGet[], spinner: () => Element | null) {
        expect(held.map(h => h.url)).toEqual(PAGE_URLS);
        expect(held.map(h => h.config?.signal?.aborted)).toEqual([false, false, false]);
        expect(spinner()).not.toBeNull();
        expect(screen.queryByText(TITLE)).toBeNull();
    }

    it('a first load that fails ends loading and shows the failure', async () => {
        quietConsole();
        const { held } = holdGets(api);
        const { container } = render(withCapabilities(<EquipmentPage />, TEST_CAPABILITIES));
        await waitFor(() => expect(held).toHaveLength(3));
        expect(spinnerIn(container)).not.toBeNull();

        await act(async () => { held[1].reject(new Error('network down')); });

        expect(spinnerIn(container)).toBeNull();
        expect(screen.getByText(LOAD_FAILED)).toBeInTheDocument();
    });

    it('Refresh shows loading again, and a failed refresh ends it', async () => {
        const { held, spinner } = await mountLoaded();

        fireEvent.click(screen.getByText('רענן'));
        expectReloadOut(held, spinner);

        await act(async () => { held[0].reject(new Error('network down')); });
        expect(spinner()).toBeNull();
        expect(screen.getByText(LOAD_FAILED)).toBeInTheDocument();
    });

    // Each write that reloads the page: what to press, and the POST it sends.
    const WRITES: Array<{ name: string; url: string; start: () => Promise<void> | void }> = [
        {
            name: 'a presence report',
            url: `/equipment/${VEST.id}/verify`,
            start: () => { fireEvent.click(screen.getByText('דווח נוכחות')); },
        },
        {
            name: 'a repair',
            url: `/maintenance/fix/${RIFLE.id}`,
            start: () => { fireEvent.click(screen.getByText('תקן')); },
        },
        {
            name: 'a fault report',
            url: '/maintenance/report',
            start: () => {
                fireEvent.click(screen.getByText('דווח תקלה'));
                const modal = modalTitled('⚠ דיווח תקלה — Vest');
                fireEvent.change(modal.getByRole('combobox'), { target: { value: String(LISTED.id) } });
                fireEvent.click(modal.getByText('דווח תקלה'));
            },
        },
        {
            name: 'a transfer',
            url: '/equipment/transfer',
            start: async () => {
                fireEvent.click(screen.getAllByText('העבר')[0]);
                const modal = modalTitled('🔄 העברת ציוד — Vest');
                await pickPerson(modal, 'חפש משתמש...');
                fireEvent.click(modal.getByText('אשר העברה'));
            },
        },
        {
            name: 'an owner assignment',
            url: '/equipment/assign_owner/',
            start: async () => {
                fireEvent.click(screen.getAllByText('שייך')[0]);
                const modal = modalTitled('👤 שיוך בעלים — Vest');
                await pickPerson(modal, 'חפש משתמש לשיוך...');
                fireEvent.click(modal.getByText('שייך בעלים'));
            },
        },
        {
            name: 'a verification form',
            url: '/verifications/',
            start: () => {
                fireEvent.click(screen.getAllByText('🔍')[0]);
                fireEvent.click(screen.getByText('שלח דיווח'));
            },
        },
    ];

    it.each(WRITES)('$name shows loading while its reload is out', async ({ url, start }) => {
        const { error, held, posts, spinner } = await mountLoaded();

        await start();
        expect(posts.map(p => p.url)).toEqual([url]);
        expect(held).toHaveLength(0);
        await act(async () => { posts[0].resolve(); });

        expectReloadOut(held, spinner);

        await act(async () => { for (const h of held) h.resolve(PAGE_DATA[h.url]); });
        expect(spinner()).toBeNull();
        expect(screen.getByText(TITLE)).toBeInTheDocument();
        expect(error).not.toHaveBeenCalled();
    });
});
