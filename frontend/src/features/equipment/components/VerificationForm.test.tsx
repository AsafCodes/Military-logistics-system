/**
 * FE-H6-2. The submit handler's `finally` became a statement after the
 * try/catch. These pin the handler's loading state around it: the button is
 * busy while the write is out, and usable again once the write has settled
 * either way.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import VerificationForm from './VerificationForm';
import api from '@/lib/axios';
import { holdPosts } from '@/test/httpStubs';

const SUBMIT = 'שלח דיווח';
const BUSY = 'שומר...';
const FAILED = 'שגיאה בשמירת הדיווח';

function renderForm() {
    const posts = holdPosts(api);
    const onSuccess = vi.fn();
    const onClose = vi.fn();
    render(
        <VerificationForm equipmentId={7} currentStatus="Functional" isOpen
            onSuccess={onSuccess} onClose={onClose} />,
    );
    return { posts, onSuccess, onClose };
}

describe('VerificationForm: submitting (FE-H6-2)', () => {
    it('is busy while the write is out, then reports success once', async () => {
        const { posts, onSuccess, onClose } = renderForm();

        fireEvent.click(screen.getByText(SUBMIT));

        expect(screen.getByText(BUSY)).toBeDisabled();
        expect(posts).toHaveLength(1);
        expect(posts[0].url).toBe('/verifications/');
        expect(posts[0].body).toEqual({
            equipment_id: 7,
            verification_type: 'presence_check',
            reported_status: 'Functional',
            findings: '',
            action_required: false,
        });

        posts[0].resolve();
        await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
        expect(onClose).toHaveBeenCalledTimes(1);
        expect(screen.queryByText(FAILED)).toBeNull();
        expect(await screen.findByText(SUBMIT)).toBeEnabled();
    });

    it('shows the failure and can be submitted again', async () => {
        const error = vi.spyOn(console, 'error').mockImplementation(() => { });
        const { posts, onSuccess, onClose } = renderForm();
        fireEvent.click(screen.getByText(SUBMIT));

        const failure = new Error('timeout of 10000ms exceeded');
        posts[0].reject(failure);

        expect(await screen.findByText(FAILED)).toBeInTheDocument();
        expect(screen.getByText(SUBMIT)).toBeEnabled();
        expect(error).toHaveBeenCalledWith(failure);
        expect(onSuccess).not.toHaveBeenCalled();
        expect(onClose).not.toHaveBeenCalled();

        // The second attempt clears the message while it is out.
        fireEvent.click(screen.getByText(SUBMIT));
        expect(posts).toHaveLength(2);
        expect(screen.getByText(BUSY)).toBeDisabled();
        expect(screen.queryByText(FAILED)).toBeNull();
    });
});
