/**
 * SEC-H10. The route guard in App.tsx is a client-side convenience; MANAGE_PERSONNEL
 * is the real gate (list_groups, backend/routers/setup.py). Someone can still reach
 * this panel with a stale capabilities snapshot or after a mid-session demotion, so
 * a failed /groups fetch must be handled -- but scoped to what actually failed.
 *
 * A code review of the first version of this fix (a whole-panel refusal screen on
 * ANY /groups failure) found it regressed two things: it hid user search, which
 * fetches independently and never depended on /groups, and it reported a plain
 * network blip using the same "you may not have permission" wording as a real 403.
 * These tests pin the corrected, scoped behavior directly.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { CanceledError } from 'axios';
import AdminPanel from './AdminPanel';
import api from '@/lib/axios';
import { holdGets, withAdapter } from '@/test/httpStubs';

const GROUPS = [{ id: 1, name: 'Company A' }];
const USER = { id: 1, full_name: 'Test User', personal_number: 'u_test' };

// The group selector -- and any error about it -- lives in the "edit" side of
// the panel, which only renders once a user is selected. Search itself never
// depends on that state, which is exactly the property these tests pin.
async function selectAUser() {
    fireEvent.change(screen.getByPlaceholderText('התחל להקליד...'), { target: { value: 'Test' } });
    const result = await screen.findByText(USER.full_name);
    fireEvent.click(result);
}

describe('AdminPanel: a failed /groups fetch is scoped, not panel-wide', () => {
    it('shows a real permission refusal on a 403, without breaking user search', async () => {
        vi.spyOn(api, 'get').mockImplementation((url: string) => {
            if (url === '/groups') return Promise.reject({ response: { status: 403 } });
            if (url === '/users') return Promise.resolve({ data: [USER] });
            return Promise.resolve({ data: [] });
        });

        render(<AdminPanel onClose={() => { }} />);

        // Search still works while /groups is broken -- it never depended on it.
        await selectAUser();

        expect(await screen.findByText(/אין לך הרשאה לצפות ברשימת הקבוצות/)).toBeInTheDocument();
        // A 403 is not transient -- retrying it tells the operator nothing new.
        expect(screen.queryByText('נסה שוב')).toBeNull();
    });

    it('offers a retry on a network failure, distinct wording from a 403, and recovers', async () => {
        const get = vi.spyOn(api, 'get').mockImplementation((url: string) => {
            if (url === '/groups') return Promise.reject(new Error('network down'));
            if (url === '/users') return Promise.resolve({ data: [USER] });
            return Promise.resolve({ data: [] });
        });

        render(<AdminPanel onClose={() => { }} />);
        await selectAUser();

        expect(await screen.findByText(/טעינת רשימת הקבוצות נכשלה/)).toBeInTheDocument();
        expect(screen.queryByText(/אין לך הרשאה/)).toBeNull();

        get.mockImplementation((url: string) => {
            if (url === '/groups') return Promise.resolve({ data: GROUPS });
            if (url === '/users') return Promise.resolve({ data: [USER] });
            return Promise.resolve({ data: [] });
        });
        fireEvent.click(screen.getByText('נסה שוב'));

        await waitFor(() => expect(screen.queryByText(/טעינת רשימת הקבוצות נכשלה/)).toBeNull());
    });

    it('renders the group list normally when the fetch succeeds', async () => {
        vi.spyOn(api, 'get').mockImplementation((url: string) => {
            if (url === '/groups') {
                return Promise.resolve({ data: GROUPS });
            }
            return Promise.resolve({ data: [] });
        });

        render(<AdminPanel onClose={() => { }} />);

        await waitFor(() => expect(api.get).toHaveBeenCalledWith('/groups'));
        expect(screen.queryByText(/טעינת רשימת הקבוצות נכשלה/)).toBeNull();
        expect(screen.queryByText(/אין לך הרשאה/)).toBeNull();
    });
});

/**
 * FE-H5. The search is debounced, but the debounce only cancels a timer that
 * has not fired. Before this fix a request already sent was left to land, so
 * an older term's response arriving after a newer one's replaced the newer
 * results. Real timers throughout: each search waits out the 300 ms debounce
 * inside waitFor's 1 s.
 */
const type = (value: string) =>
    fireEvent.change(screen.getByPlaceholderText('התחל להקליד...'), { target: { value } });

describe('AdminPanel user search: a newer term cancels the older request (FE-H5)', () => {
    const OLDER = { id: 2, full_name: 'Ab Older', personal_number: 'u_ab' };
    const NEWER = { id: 3, full_name: 'Abc Newer', personal_number: 'u_abc' };
    // Drains the promise jobs a settled request queues, and the render they cause.
    const settle = () => act(async () => { });

    async function searchAbThenAbc() {
        const { held } = holdGets(api, { '/groups': GROUPS });
        render(<AdminPanel onClose={() => { }} />);
        type('ab');
        await waitFor(() => expect(held).toHaveLength(1));
        type('abc');
        await waitFor(() => expect(held).toHaveLength(2));
        return held;
    }

    it("aborts the older request and shows only the newer term's results", async () => {
        const [ab, abc] = await searchAbThenAbc();

        expect(ab.url).toBe('/users');
        expect(ab.config?.params).toEqual({ q: 'ab' });
        expect(abc.config?.params).toEqual({ q: 'abc' });
        expect(ab.config?.signal?.aborted).toBe(true);
        expect(abc.config?.signal?.aborted).toBe(false);

        abc.resolve([NEWER]);
        expect(await screen.findByText(NEWER.full_name)).toBeInTheDocument();

        // The older response arrives last. Through the real client it would
        // reject as a CanceledError; here it resolves, so the panel's own
        // post-await check is what has to drop it.
        ab.resolve([OLDER]);
        await settle();
        expect(screen.queryByText(OLDER.full_name)).toBeNull();
        expect(screen.getByText(NEWER.full_name)).toBeInTheDocument();
    });

    it('treats the abort as no error and leaves the newer search showing as in progress', async () => {
        const error = vi.spyOn(console, 'error').mockImplementation(() => { });
        const [ab] = await searchAbThenAbc();

        ab.reject(new CanceledError());
        await settle();

        expect(error).not.toHaveBeenCalled();
        // "abc" is still out. Clearing the flag here would show "no users
        // found" for a search that hasn't answered yet.
        expect(screen.getByText('מחפש...')).toBeInTheDocument();
        expect(screen.queryByText('לא נמצאו משתמשים.')).toBeNull();
    });

    it('stops showing the search in progress when the term shrinks below two characters', async () => {
        const { held } = holdGets(api, { '/groups': GROUPS });
        render(<AdminPanel onClose={() => { }} />);
        type('ab');
        await waitFor(() => expect(held).toHaveLength(1));
        expect(screen.getByText('מחפש...')).toBeInTheDocument();

        type('a');
        expect(held[0].config?.signal?.aborted).toBe(true);
        held[0].reject(new CanceledError());

        // The aborted run leaves the flag alone, so the short-term branch is
        // the one that must clear it.
        await waitFor(() => expect(screen.queryByText('מחפש...')).toBeNull());
        expect(held).toHaveLength(1);
    });

    it('still reports a real failure and stops showing the search in progress', async () => {
        // The abort guard must not swallow errors that are not aborts. A
        // timeout is one: axios rejects it without aborting the signal.
        const error = vi.spyOn(console, 'error').mockImplementation(() => { });
        const { held } = holdGets(api, { '/groups': GROUPS });
        render(<AdminPanel onClose={() => { }} />);
        type('ab');
        await waitFor(() => expect(held).toHaveLength(1));

        const failure = new Error('timeout of 10000ms exceeded');
        held[0].reject(failure);

        await waitFor(() => expect(screen.queryByText('מחפש...')).toBeNull());
        expect(error).toHaveBeenCalledWith(failure);
    });

    it('aborts a search still in flight when the panel unmounts', async () => {
        const { held } = holdGets(api, { '/groups': GROUPS });
        const { unmount } = render(<AdminPanel onClose={() => { }} />);
        type('ab');
        await waitFor(() => expect(held).toHaveLength(1));

        unmount();
        expect(held[0].config?.signal?.aborted).toBe(true);
    });
});

describe('AdminPanel user search: the term is URL-encoded (FE-L6)', () => {
    it('sends the term as an encoded parameter, so &, #, + and % reach the server intact', async () => {
        // Through the real client, so axios's own URL building is what's tested.
        const { install, restore } = withAdapter(api);
        const urls: string[] = [];
        install(async config => {
            urls.push(api.getUri(config));
            const data = config.url === '/groups' ? GROUPS : [];
            return { data, status: 200, statusText: 'OK', headers: {}, config };
        });
        try {
            // Hebrew and a space too: axios writes the space as `+`, which a
            // query-string parser reads back as a space; a literal `+` must
            // go as %2B or it would come back as a space as well.
            const term = 'כהן A&B#1+c 100%';
            render(<AdminPanel onClose={() => { }} />);
            type(term);
            await waitFor(() => expect(urls.some(u => new URL(u).pathname === '/users')).toBe(true));

            const sent = new URL(urls.find(u => new URL(u).pathname === '/users')!);
            expect(sent.searchParams.get('q')).toBe(term);
            expect([...sent.searchParams.keys()]).toEqual(['q']);
            expect(sent.hash).toBe('');
        } finally {
            restore();
        }
    });
});
