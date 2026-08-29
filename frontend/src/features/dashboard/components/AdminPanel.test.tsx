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
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import AdminPanel from './AdminPanel';
import api from '@/api';

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
            if (url.startsWith('/users?q=')) return Promise.resolve({ data: [USER] });
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
            if (url.startsWith('/users?q=')) return Promise.resolve({ data: [USER] });
            return Promise.resolve({ data: [] });
        });

        render(<AdminPanel onClose={() => { }} />);
        await selectAUser();

        expect(await screen.findByText(/טעינת רשימת הקבוצות נכשלה/)).toBeInTheDocument();
        expect(screen.queryByText(/אין לך הרשאה/)).toBeNull();

        get.mockImplementation((url: string) => {
            if (url === '/groups') return Promise.resolve({ data: GROUPS });
            if (url.startsWith('/users?q=')) return Promise.resolve({ data: [USER] });
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
