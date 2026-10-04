/**
 * API-H6. The approval queue for fault types that report_fault minted as
 * pending. Before this page, PUT /setup/fault_types/{id}/approve had no caller,
 * so a reporter's new type stayed hidden from EquipmentPage's dropdown for good.
 *
 * The backend is the real gate (require_global(MANAGE_CATALOG) on both
 * routes); App.test.tsx pins who gets the route at all. What these tests pin is
 * the page's own behaviour: what it asks for, what it shows, and that a failed
 * approval never looks like a successful one.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { renderToStaticMarkup } from 'react-dom/server';
import FaultTypeQueuePage from './FaultTypeQueuePage';
import api from '@/lib/axios';
import { withAdapter, failingWith, stubLocation, restoreLocation } from '@/test/httpStubs';
import type { FaultType } from '@/types';

const PENDING = [
    { id: 7, name: 'Cracked Housing', is_pending: true },
    { id: 9, name: 'Frayed Strap', is_pending: true },
] satisfies FaultType[];

const PENDING_URL = '/setup/fault_types/pending';
const APPROVE = 'אשר';

function serving(rows: FaultType[]) {
    return vi.spyOn(api, 'get').mockImplementation((url: string) =>
        url === PENDING_URL
            ? Promise.resolve({ data: rows })
            : Promise.reject(new Error(`unexpected GET ${url}`)),
    );
}

// The row's own Approve button, by its accessible name, so a test cannot press
// the wrong row's button and still pass -- and so the label that tells the
// buttons apart to a screen reader is asserted by every test that presses one.
function approveButtonFor(name: string): HTMLElement {
    return screen.getByRole('button', { name: `אשר את ${name}` });
}

describe('FaultTypeQueuePage', () => {
    it('lists the pending queue it fetched', async () => {
        const get = serving(PENDING);

        render(<FaultTypeQueuePage />);

        expect(await screen.findByText('Cracked Housing')).toBeInTheDocument();
        expect(screen.getByText('Frayed Strap')).toBeInTheDocument();
        expect(get).toHaveBeenCalledWith(PENDING_URL);
        expect(screen.getAllByText(APPROVE)).toHaveLength(2);
    });

    it('renders a hostile type name as text, not markup', async () => {
        // These names are free text from accounts holding no grant -- an
        // item's holder reporting a fault, or anyone at all through
        // POST /setup/fault_types -- and this page shows them to the most
        // privileged accounts there are.
        // React escapes them; this pins that nothing here opts out.
        const HOSTILE = '<img src=x onerror="window.__pwned=1">';
        serving([{ id: 3, name: HOSTILE, is_pending: true }]);

        const { container } = render(<FaultTypeQueuePage />);

        expect(await screen.findByText(HOSTILE)).toBeInTheDocument();
        expect(container.querySelector('img')).toBeNull();
    });

    it('paints loading, not an empty queue, on its very first frame', () => {
        // render() cannot see this frame: it runs inside act(), which flushes
        // the mount effect -- and fetchPending's setLoading(true) -- before the
        // first assertion. So `useState(false)` passed every other test here
        // and the whole suite, while a browser would paint "nothing pending"
        // for a frame before the effect ran (the mutation battery found it).
        // Server rendering runs no effects, so its markup IS the first commit.
        vi.spyOn(api, 'get').mockImplementation(() => new Promise(() => { }));

        const firstFrame = renderToStaticMarkup(<FaultTypeQueuePage />);

        expect(firstFrame).toContain('טוען...');
        expect(firstFrame).not.toContain('אין סוגי תקלות הממתינים לאישור');
    });

    it('does not claim an empty queue before the fetch settles', async () => {
        // Never resolves: the page must sit in loading, not in "nothing pending".
        vi.spyOn(api, 'get').mockImplementation(() => new Promise(() => { }));

        render(<FaultTypeQueuePage />);

        expect(screen.getByText('טוען...')).toBeInTheDocument();
        expect(screen.queryByText(/אין סוגי תקלות הממתינים לאישור/)).toBeNull();
    });

    it('says so when nothing is pending', async () => {
        serving([]);

        render(<FaultTypeQueuePage />);

        expect(await screen.findByText(/אין סוגי תקלות הממתינים לאישור/)).toBeInTheDocument();
        expect(screen.queryByText(APPROVE)).toBeNull();
    });

    it('approves the row that was pressed, by id, and removes only that row', async () => {
        serving(PENDING);
        const put = vi.spyOn(api, 'put').mockResolvedValue({ data: { status: 'Approved', id: 9 } });

        render(<FaultTypeQueuePage />);
        await screen.findByText('Frayed Strap');
        fireEvent.click(approveButtonFor('Frayed Strap'));

        await waitFor(() => expect(screen.queryByText('Frayed Strap')).toBeNull());
        expect(put).toHaveBeenCalledTimes(1);
        expect(put).toHaveBeenCalledWith('/setup/fault_types/9/approve');
        expect(screen.getByText('Cracked Housing')).toBeInTheDocument();
    });

    it('sends one approval for a double click, not two', async () => {
        serving(PENDING);
        let settle: (v: unknown) => void = () => { };
        const put = vi.spyOn(api, 'put').mockImplementation(
            () => new Promise(resolve => { settle = resolve; }),
        );

        render(<FaultTypeQueuePage />);
        await screen.findByText('Cracked Housing');
        const button = approveButtonFor('Cracked Housing');
        fireEvent.click(button);
        fireEvent.click(button);

        expect(button).toBeDisabled();
        // The other row is not caught up in this one's request.
        expect(approveButtonFor('Frayed Strap')).not.toBeDisabled();
        expect(put).toHaveBeenCalledTimes(1);

        settle({ data: {} });
        await waitFor(() => expect(screen.queryByText('Cracked Housing')).toBeNull());
    });

    it('drops a row the server no longer has (404), without an error', async () => {
        serving(PENDING);
        vi.spyOn(api, 'put').mockRejectedValue({ response: { status: 404 } });
        const alerted = vi.spyOn(window, 'alert').mockImplementation(() => { });

        render(<FaultTypeQueuePage />);
        await screen.findByText('Cracked Housing');
        fireEvent.click(approveButtonFor('Cracked Housing'));

        await waitFor(() => expect(screen.queryByText('Cracked Housing')).toBeNull());
        expect(alerted).not.toHaveBeenCalled();
    });

    it('keeps the row and says so when an approval fails', async () => {
        // The failure mode that matters: a row that vanished on a 500 would tell
        // the approver the type is now in the dropdown when it is not.
        serving(PENDING);
        vi.spyOn(api, 'put').mockRejectedValue({ response: { status: 500 } });
        vi.spyOn(console, 'error').mockImplementation(() => { });
        const alerted = vi.spyOn(window, 'alert').mockImplementation(() => { });

        render(<FaultTypeQueuePage />);
        await screen.findByText('Cracked Housing');
        fireEvent.click(approveButtonFor('Cracked Housing'));

        await waitFor(() => expect(alerted).toHaveBeenCalledWith('אישור סוג התקלה נכשל.'));
        expect(screen.getByText('Cracked Housing')).toBeInTheDocument();
        // And it can be tried again.
        await waitFor(() => expect(approveButtonFor('Cracked Housing')).not.toBeDisabled());
    });

    it('shows a real permission refusal on a 403, with no retry', async () => {
        vi.spyOn(api, 'get').mockRejectedValue({ response: { status: 403 } });
        vi.spyOn(console, 'error').mockImplementation(() => { });

        render(<FaultTypeQueuePage />);

        expect(await screen.findByText(/אין לך הרשאה לצפות בתור האישורים/)).toBeInTheDocument();
        // A 403 is not transient -- retrying it tells the operator nothing new.
        expect(screen.queryByText('נסה שוב')).toBeNull();
        expect(screen.queryByText(/אין סוגי תקלות הממתינים לאישור/)).toBeNull();
    });

    describe('an expired session (401), through the real client', () => {
        // Every other test here spies on api.get, which skips the interceptors
        // entirely. This one stubs the transport instead, so the request runs
        // the shared client's real 401 policy -- the one FE-H1 gave every
        // feature page in place of api.ts's window.location.reload().
        let adapter: ReturnType<typeof withAdapter>;

        beforeEach(() => { adapter = withAdapter(api); });
        afterEach(() => {
            adapter.restore();
            restoreLocation();
        });

        it('navigates to /login without reloading, and still shows the failure', async () => {
            vi.spyOn(console, 'error').mockImplementation(() => { });
            const { reload } = stubLocation('/catalog');
            adapter.install(failingWith(401));

            render(<FaultTypeQueuePage />);

            // The interceptor still rejects, so the page leaves its spinner
            // instead of waiting forever for an answer that is not coming.
            expect(await screen.findByText(/טעינת התור נכשלה/)).toBeInTheDocument();
            expect(window.location.href).toBe('/login');
            expect(reload).not.toHaveBeenCalled();
        });
    });

    it('offers a retry on a network failure, worded apart from a 403, and recovers', async () => {
        vi.spyOn(console, 'error').mockImplementation(() => { });
        const get = vi.spyOn(api, 'get').mockRejectedValue(new Error('network down'));

        render(<FaultTypeQueuePage />);

        expect(await screen.findByText(/טעינת התור נכשלה/)).toBeInTheDocument();
        expect(screen.queryByText(/אין לך הרשאה/)).toBeNull();

        get.mockResolvedValue({ data: PENDING });
        fireEvent.click(screen.getByText('נסה שוב'));

        expect(await screen.findByText('Cracked Housing')).toBeInTheDocument();
        expect(screen.queryByText(/טעינת התור נכשלה/)).toBeNull();
    });
});
