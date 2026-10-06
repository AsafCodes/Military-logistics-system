/**
 * FE-H5. useLatestRequest hands out one live signal at a time: a new one
 * aborts the last, and unmounting aborts whichever is current.
 */
import { useEffect } from 'react';
import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useLatestRequest } from './useLatestRequest';

describe('useLatestRequest (FE-H5)', () => {
    it('U1: next() aborts the signal the previous call handed out', () => {
        const { result } = renderHook(() => useLatestRequest());

        const first = result.current();
        expect(first.aborted).toBe(false);

        const second = result.current();
        expect(first.aborted).toBe(true);
        expect(second.aborted).toBe(false);
        expect(second).not.toBe(first);
    });

    it('U2: unmounting aborts the current signal', () => {
        const { result, unmount } = renderHook(() => useLatestRequest());
        const signal = result.current();

        unmount();

        expect(signal.aborted).toBe(true);
    });

    it('U2: unmounting before any next() is harmless', () => {
        const { unmount } = renderHook(() => useLatestRequest());
        expect(() => unmount()).not.toThrow();
    });

    it('U3: under StrictMode a mount-time load leaves [aborted, live]', () => {
        // StrictMode mounts, unmounts and remounts the effects once in
        // development, so a load started from a mount effect runs twice.
        const signals: AbortSignal[] = [];
        const { unmount } = renderHook(
            () => {
                const next = useLatestRequest();
                useEffect(() => { signals.push(next()); }, [next]);
            },
            { reactStrictMode: true },
        );

        expect(signals.map(s => s.aborted)).toEqual([true, false]);

        unmount();
        expect(signals.map(s => s.aborted)).toEqual([true, true]);
    });

    it('U5: next() after unmount hands out a signal that is already aborted', () => {
        // A write still out when the page is left calls its reload afterwards.
        const { result, unmount } = renderHook(() => useLatestRequest());
        const next = result.current;
        next();

        unmount();

        expect(next().aborted).toBe(true);
        expect(next().aborted).toBe(true);
    });

    it('U4: next keeps its identity across renders', () => {
        const { result, rerender } = renderHook(() => useLatestRequest());
        const first = result.current;

        first();
        rerender();

        expect(result.current).toBe(first);
    });
});
