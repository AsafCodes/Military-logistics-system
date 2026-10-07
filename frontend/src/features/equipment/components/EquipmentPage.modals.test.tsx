/**
 * FE-H6-2. The three write modals on the equipment page. Each submit
 * handler's `finally` became a statement after the try/catch, and the two
 * user-search lists are now cleared by the handlers that end a search instead
 * of by the search effect. These pin the behaviour around the rewritten code:
 * what a submit shows and sends, and when a search list empties.
 *
 * The modals are module-local, so they are reached through the page, as in
 * EquipmentPage.test.tsx.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, fireEvent, within, act } from '@testing-library/react';
import EquipmentPage from './EquipmentPage';
import type { FaultType } from '@/types';
import { TEST_USER, TEST_CAPABILITIES, withCapabilities } from '@/test/setup';
import { holdGets, holdPosts } from '@/test/httpStubs';
import api from '@/lib/axios';

const ITEM = {
    id: 20, type: 'Vest', item_name: 'Vest', status: 'Functional',
    current_state_description: '', compliance_check: '', report_status: '',
    compliance_level: 'NEUTRAL', holder_user_id: TEST_USER.id, serial_number: 'F1',
};
const LISTED = { id: 3, name: 'Cracked Housing', is_pending: false } satisfies FaultType;
const PERSON = { id: 4, full_name: 'Dana Levi', personal_number: 'u_dana' };

const RELOADED_URL = '/equipment/accessible';

/** Renders the page with its three loads answered, and opens one modal. */
async function openModal(opens: string, title: string) {
    const gets = holdGets(api, {
        '/users/me': TEST_USER,
        [RELOADED_URL]: [ITEM],
        '/setup/fault_types': [LISTED],
    });
    const posts = holdPosts(api);
    const alert = vi.spyOn(window, 'alert').mockImplementation(() => { });
    render(withCapabilities(<EquipmentPage />, TEST_CAPABILITIES));
    // Three seconds, as in App.test.tsx: the file's first render of the
    // page also pays for loading the modules.
    fireEvent.click(await screen.findByText(opens, {}, { timeout: 3000 }));
    const heading = screen.getByText(title);
    const card = heading.closest('.glass-card');
    if (!(card instanceof HTMLElement)) throw new Error('the modal did not render');
    const pageLoads = () => gets.spy.mock.calls.filter(([url]) => url === RELOADED_URL).length;
    return { modal: within(card), heading, gets, posts, alert, pageLoads };
}

type Opened = Awaited<ReturnType<typeof openModal>>;

/** Types a term, answers its search with PERSON and picks that person. */
async function pickPerson({ modal, gets }: Opened, placeholder: string) {
    fireEvent.change(modal.getByPlaceholderText(placeholder), { target: { value: 'da' } });
    await waitFor(() => expect(gets.held).toHaveLength(1));
    gets.held[0].resolve([PERSON]);
    fireEvent.click(await modal.findByText(PERSON.full_name));
}

const MODALS = [
    {
        name: 'report fault',
        opens: 'דווח תקלה', title: '⚠ דיווח תקלה — Vest',
        submit: 'דווח תקלה', busy: 'שולח...', failed: 'דיווח התקלה נכשל',
        fill: async ({ modal }: Opened) => {
            fireEvent.change(modal.getByRole('combobox'), { target: { value: String(LISTED.id) } });
        },
        url: '/maintenance/report',
        body: { equipment_id: ITEM.id, fault_name: LISTED.name, description: '' },
    },
    {
        name: 'transfer',
        opens: 'העבר', title: '🔄 העברת ציוד — Vest',
        submit: 'אשר העברה', busy: 'מעביר...', failed: 'העברה נכשלה',
        fill: (opened: Opened) => pickPerson(opened, 'חפש משתמש...'),
        url: '/equipment/transfer',
        body: { equipment_id: ITEM.id, to_holder_id: PERSON.id },
    },
    {
        name: 'assign owner',
        opens: 'שייך', title: '👤 שיוך בעלים — Vest',
        submit: 'שייך בעלים', busy: 'משייך...', failed: 'שיוך נכשל',
        fill: (opened: Opened) => pickPerson(opened, 'חפש משתמש לשיוך...'),
        url: '/equipment/assign_owner/',
        body: { equipment_id: ITEM.id, owner_id: PERSON.id },
    },
];

describe.each(MODALS)('EquipmentPage $name modal: submitting (FE-H6-2)', (m) => {
    it('is busy while the write is out, then closes and reloads the page', async () => {
        const opened = await openModal(m.opens, m.title);
        const { modal, posts, alert, pageLoads } = opened;
        await m.fill(opened);
        expect(pageLoads()).toBe(1);

        fireEvent.click(modal.getByText(m.submit));

        expect(modal.getByText(m.busy)).toBeDisabled();
        expect(posts.map(p => [p.url, p.body])).toEqual([[m.url, m.body]]);

        await act(async () => { posts[0].resolve(); });
        expect(pageLoads()).toBe(2);
        // The reload swaps the page for a spinner and back, so the modal is
        // looked for afresh once the row is there again: a modal left open
        // would have been drawn again with it.
        expect(await screen.findByText(m.opens)).toBeInTheDocument();
        expect(screen.queryByText(m.title)).toBeNull();
        expect(alert).not.toHaveBeenCalled();
    });

    it('reports a failed write, stays open and can be submitted again', async () => {
        const error = vi.spyOn(console, 'error').mockImplementation(() => { });
        const opened = await openModal(m.opens, m.title);
        const { modal, heading, posts, alert, pageLoads } = opened;
        await m.fill(opened);
        fireEvent.click(modal.getByText(m.submit));

        const failure = new Error('timeout of 10000ms exceeded');
        await act(async () => { posts[0].reject(failure); });

        expect(alert.mock.calls).toEqual([[m.failed]]);
        expect(error).toHaveBeenCalledWith(failure);
        expect(heading).toBeInTheDocument();
        expect(modal.getByText(m.submit)).toBeEnabled();
        expect(pageLoads()).toBe(1);

        fireEvent.click(modal.getByText(m.submit));
        expect(posts).toHaveLength(2);
    });
});

describe('EquipmentPage modals: what the write carries (FE-H6-2)', () => {
    it('report fault sends the typed name for a new fault type', async () => {
        const { modal, posts } = await openModal('דווח תקלה', '⚠ דיווח תקלה — Vest');
        fireEvent.change(modal.getByRole('combobox'), { target: { value: 'other' } });
        fireEvent.change(modal.getByPlaceholderText('הזן שם תקלה...'), { target: { value: 'Torn Strap' } });
        fireEvent.change(modal.getByPlaceholderText('תאר את התקלה...'), { target: { value: 'left side' } });

        fireEvent.click(modal.getByText('דווח תקלה'));

        expect(posts[0].body).toEqual({ equipment_id: ITEM.id, fault_name: 'Torn Strap', description: 'left side' });
    });

    it('transfer sends a location, and no holder, in location mode', async () => {
        const { modal, posts } = await openModal('העבר', '🔄 העברת ציוד — Vest');
        fireEvent.click(modal.getByText('📍 העבר למיקום'));
        fireEvent.change(modal.getByPlaceholderText('שם מיקום...'), { target: { value: 'Armory' } });

        fireEvent.click(modal.getByText('אשר העברה'));

        expect(posts[0].body).toEqual({ equipment_id: ITEM.id, to_location: 'Armory' });
    });
});

describe.each([
    { opens: 'העבר', title: '🔄 העברת ציוד — Vest', placeholder: 'חפש משתמש...' },
    { opens: 'שייך', title: '👤 שיוך בעלים — Vest', placeholder: 'חפש משתמש לשיוך...' },
])('EquipmentPage $opens modal search: the list and the term (FE-H6-2)', ({ opens, title, placeholder }) => {
    // Real timers: the debounce is 300 ms.
    const pastDebounce = () => act(() => new Promise<void>(resolve => { setTimeout(resolve, 400); }));

    it('empties the list as soon as the term drops below two characters', async () => {
        const { modal, gets } = await openModal(opens, title);
        const input = modal.getByPlaceholderText(placeholder);
        fireEvent.change(input, { target: { value: 'da' } });
        await waitFor(() => expect(gets.held).toHaveLength(1));
        gets.held[0].resolve([PERSON]);
        expect(await modal.findByText(PERSON.full_name)).toBeInTheDocument();

        fireEvent.change(input, { target: { value: 'd' } });

        expect(modal.queryByText(PERSON.full_name)).toBeNull();
        await pastDebounce();
        expect(gets.held).toHaveLength(1);
    });

    it('keeps the list while a term of two characters is being searched', async () => {
        const { modal, gets } = await openModal(opens, title);
        const input = modal.getByPlaceholderText(placeholder);
        fireEvent.change(input, { target: { value: 'dan' } });
        await waitFor(() => expect(gets.held).toHaveLength(1));
        gets.held[0].resolve([PERSON]);
        expect(await modal.findByText(PERSON.full_name)).toBeInTheDocument();

        fireEvent.change(input, { target: { value: 'da' } });

        expect(modal.getByText(PERSON.full_name)).toBeInTheDocument();
        await waitFor(() => expect(gets.held).toHaveLength(2));
        expect(gets.held[1].config?.params).toEqual({ q: 'da' });
    });

    it('drops the picked person when the term is edited', async () => {
        const opened = await openModal(opens, title);
        const { modal } = opened;
        await pickPerson(opened, placeholder);
        const submit = modal.getAllByRole('button').at(-1);
        expect(submit).toBeEnabled();

        fireEvent.change(modal.getByPlaceholderText(placeholder), { target: { value: 'Dana Lev' } });

        expect(submit).toBeDisabled();
    });

    it('sends nothing for a one-character term', async () => {
        const { modal, gets } = await openModal(opens, title);
        fireEvent.change(modal.getByPlaceholderText(placeholder), { target: { value: 'd' } });
        await pastDebounce();
        expect(gets.held).toHaveLength(0);
    });
});

describe('EquipmentPage transfer modal: switching to a location clears the list (FE-H6-2)', () => {
    it('shows no earlier results on switching back, until the new search answers', async () => {
        const { modal, gets } = await openModal('העבר', '🔄 העברת ציוד — Vest');
        fireEvent.change(modal.getByPlaceholderText('חפש משתמש...'), { target: { value: 'da' } });
        await waitFor(() => expect(gets.held).toHaveLength(1));
        gets.held[0].resolve([PERSON]);
        expect(await modal.findByText(PERSON.full_name)).toBeInTheDocument();

        fireEvent.click(modal.getByText('📍 העבר למיקום'));
        fireEvent.click(modal.getByText('👤 העבר לאדם'));

        expect(modal.queryByText(PERSON.full_name)).toBeNull();
        await waitFor(() => expect(gets.held).toHaveLength(2));
        gets.held[1].resolve([PERSON]);
        expect(await modal.findByText(PERSON.full_name)).toBeInTheDocument();
    });
});
