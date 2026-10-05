/**
 * FE-H5. The history modal's read belongs to one (isOpen, equipmentId) pair.
 * Closing the modal or switching to another item aborts the read in flight.
 * A late answer for the old item must not replace the new item's history, and
 * the cancelled read must not clear the loading state the new read owns.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import { CanceledError } from 'axios';
import EquipmentHistory from './EquipmentHistory';
import api from '@/lib/axios';
import { holdGets } from '@/test/httpStubs';

const row = (equipmentId: number) => ({
    id: equipmentId * 10,
    equipment_id: equipmentId,
    old_status: 'Functional',
    new_status: 'Malfunctioning',
    change_reason: 'fault',
    verification_id: null,
    notes: `note for item ${equipmentId}`,
    created_date: '2026-06-15T09:30:00Z',
    created_by: 1,
    user_name: null,
});

const LOADING = 'טוען...';
const EMPTY = 'אין היסטוריה זמינה';

function renderModal(equipmentId: number) {
    const { held } = holdGets(api);
    const view = render(<EquipmentHistory equipmentId={equipmentId} isOpen onClose={() => { }} />);
    const rerender = (id: number, isOpen = true) =>
        view.rerender(<EquipmentHistory equipmentId={id} isOpen={isOpen} onClose={() => { }} />);
    return { held, rerender };
}

describe('EquipmentHistory: one read per open item (FE-H5)', () => {
    it("drops a late answer for the previous item once the next item's history is shown", async () => {
        const { held, rerender } = renderModal(7);
        await waitFor(() => expect(held).toHaveLength(1));

        rerender(8);
        await waitFor(() => expect(held).toHaveLength(2));
        expect(held.map(h => h.url)).toEqual(['/equipment/7/history', '/equipment/8/history']);
        expect(held.map(h => h.config?.signal?.aborted)).toEqual([true, false]);

        held[1].resolve([row(8)]);
        expect(await screen.findByText('note for item 8')).toBeInTheDocument();
        held[0].resolve([row(7)]);
        await act(async () => { });

        expect(screen.queryByText('note for item 7')).toBeNull();
        expect(screen.getByText('note for item 8')).toBeInTheDocument();
    });

    it("keeps showing the next item's load when the previous read is cancelled", async () => {
        const error = vi.spyOn(console, 'error').mockImplementation(() => { });
        const { held, rerender } = renderModal(7);
        await waitFor(() => expect(held).toHaveLength(1));
        rerender(8);
        await waitFor(() => expect(held).toHaveLength(2));

        held[0].reject(new CanceledError());
        await act(async () => { });

        expect(screen.getByText(LOADING)).toBeInTheDocument();
        expect(screen.queryByText(EMPTY)).toBeNull();
        expect(error).not.toHaveBeenCalled();

        held[1].resolve([row(8)]);
        expect(await screen.findByText('note for item 8')).toBeInTheDocument();
    });

    it('aborts the read when the modal closes', async () => {
        const { held, rerender } = renderModal(7);
        await waitFor(() => expect(held).toHaveLength(1));

        rerender(7, false);

        expect(held[0].config?.signal?.aborted).toBe(true);
    });

    it('still logs a real failure and stops loading', async () => {
        const error = vi.spyOn(console, 'error').mockImplementation(() => { });
        const { held } = renderModal(7);
        await waitFor(() => expect(held).toHaveLength(1));

        const failure = new Error('timeout of 10000ms exceeded');
        held[0].reject(failure);

        expect(await screen.findByText(EMPTY)).toBeInTheDocument();
        expect(error).toHaveBeenCalledWith('Failed to fetch history:', failure);
    });
});
