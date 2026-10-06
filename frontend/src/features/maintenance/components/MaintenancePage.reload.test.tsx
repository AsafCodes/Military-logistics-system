/**
 * FE-H5. Closing a ticket reloads the list once its POST answers. The list
 * hides behind a spinner during a reload, so one Refresh can't race another,
 * but two closes started before either answers each bring their own reload.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import MaintenancePage from './MaintenancePage';
import { TEST_CAPABILITIES, withCapabilities } from '@/test/setup';
import api from '@/lib/axios';
import { holdGets } from '@/test/httpStubs';

const ticket = (id: number, name: string) => ({
    id, equipment_id: id * 10, equipment_name: name, fault_type: 'Jammed',
    description: '', status: 'Open', opened_at: '2026-01-01T00:00:00Z',
});

/** Hold every POST open; the test answers each one by index. */
function holdPosts() {
    const answers: Array<() => void> = [];
    vi.spyOn(api, 'post').mockImplementation(
        () => new Promise(resolve => { answers.push(() => resolve({ data: {} })); }),
    );
    return answers;
}

// Mount with two open tickets on screen and every later request held.
async function mountWithTwoTickets() {
    const error = vi.spyOn(console, 'error').mockImplementation(() => { });
    const posts = holdPosts();
    const { held } = holdGets(api);
    const view = render(withCapabilities(<MaintenancePage />, TEST_CAPABILITIES));
    await waitFor(() => expect(held).toHaveLength(1));
    await act(async () => { held[0].resolve([ticket(1, 'Rifle'), ticket(2, 'Radio')]); });
    expect(screen.getAllByText(/סגור כרטיס/)).toHaveLength(2);
    return { error, posts, held, ...view };
}

describe('MaintenancePage: reloads after closing tickets (FE-H5)', () => {
    it("two closes: the second reload aborts the first, whose late answer is dropped", async () => {
        const { error, posts, held } = await mountWithTwoTickets();

        const [closeRifle, closeRadio] = screen.getAllByText(/סגור כרטיס/);
        fireEvent.click(closeRifle);
        fireEvent.click(closeRadio);
        expect(posts).toHaveLength(2);

        await act(async () => { posts[0](); });
        await act(async () => { posts[1](); });
        expect(held.map(h => h.url)).toEqual(['/tickets/', '/tickets/', '/tickets/']);
        expect(held.slice(1).map(h => h.config?.signal?.aborted)).toEqual([true, false]);

        // The second reload saw both tickets closed; the first, sent between
        // the two closes, still lists the radio.
        await act(async () => { held[2].resolve([]); });
        expect(screen.queryByText('Radio')).toBeNull();

        await act(async () => { held[1].resolve([ticket(2, 'Radio')]); });
        expect(screen.queryByText('Radio')).toBeNull();
        expect(error).not.toHaveBeenCalled();
    });

    it('a close that answers after the page is left starts no live reload', async () => {
        const { error, posts, held, unmount } = await mountWithTwoTickets();

        fireEvent.click(screen.getAllByText(/סגור כרטיס/)[0]);
        unmount();
        await act(async () => { posts[0](); });

        // The handler still calls its reload. The signal it takes is aborted
        // from the start, and the real client sends nothing for such a signal.
        expect(held).toHaveLength(2);
        expect(held[1].config?.signal?.aborted).toBe(true);
        expect(error).not.toHaveBeenCalled();
    });
});
