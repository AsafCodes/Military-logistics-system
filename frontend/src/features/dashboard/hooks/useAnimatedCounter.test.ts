/**
 * FE-H4. The counter's frame loop used to have no cleanup: an unmount kept
 * scheduling frames, and a new target left the old loop running, beside a
 * second one (or, for a target of 0, overwriting the 0). Every run also eased
 * from 0, so a new target rewound the count.
 *
 * The main describe fakes only requestAnimationFrame/cancelAnimationFrame;
 * setTimeout stays real. vi.getTimerCount() then counts exactly the pending
 * frames. Fake frames
 * land every 16 ms, and the hook's clock starts at its first frame, so with a
 * 1200 ms duration a run's first frame is a no-op and its 76th lands on the
 * target.
 *
 * frames() advances one frame per act(). A single act() over many frames fires
 * them all before React renders once, which hides a loop that restarts on
 * every render.
 *
 * The last describe stubs the frame functions by hand instead, to choose each
 * frame's timestamp.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useAnimatedCounter } from './useAnimatedCounter';

const FRAME_MS = 16;
// A full run: one no-op frame that starts the clock, then 1200 / 16 = 75.
const FULL_RUN = 76;

function frames(n: number) {
    for (let i = 0; i < n; i++) {
        act(() => { vi.advanceTimersByTime(FRAME_MS); });
    }
}

function counter(target: number, duration?: number) {
    return renderHook(
        ({ target, duration }) => useAnimatedCounter(target, duration),
        { initialProps: { target, duration } },
    );
}

describe('useAnimatedCounter', () => {
    beforeEach(() => {
        vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame'] });
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it('lands on the rounded target at the end of the duration, then stops', () => {
        // 83.7, not 83.3: floor and trunc would also give 83 for the latter.
        const { result } = counter(83.7);
        expect(result.current).toBe(0);
        expect(vi.getTimerCount()).toBe(1);

        frames(FULL_RUN / 2);
        expect(result.current).toBeGreaterThan(0);
        expect(result.current).toBeLessThan(84);

        frames(FULL_RUN / 2 - 1);
        expect(vi.getTimerCount()).toBe(1);   // one frame short: still running

        frames(1);
        expect(result.current).toBe(84);
        expect(vi.getTimerCount()).toBe(0);
    });

    it('cancels the latest frame, not the first, when unmounted mid-animation', () => {
        const { unmount } = counter(100);
        frames(6);   // the first frame id has long since fired
        expect(vi.getTimerCount()).toBe(1);

        unmount();
        expect(vi.getTimerCount()).toBe(0);
    });

    it('runs exactly one loop under StrictMode', () => {
        const { result } = renderHook(() => useAnimatedCounter(100), { reactStrictMode: true });
        // StrictMode mounts, cleans up and mounts again; the first frame must go.
        expect(vi.getTimerCount()).toBe(1);

        frames(FULL_RUN);
        expect(result.current).toBe(100);
        expect(vi.getTimerCount()).toBe(0);
    });

    it('continues from the shown value when the target changes mid-animation', () => {
        const { result, rerender } = counter(100);
        frames(25);
        const shown = result.current;
        expect(shown).toBeGreaterThan(0);
        expect(shown).toBeLessThan(100);

        rerender({ target: 200, duration: undefined });
        expect(result.current).toBe(shown);
        expect(vi.getTimerCount()).toBe(1);   // the old loop is gone, one new one

        let previous = shown;
        for (let i = 0; i < FULL_RUN; i++) {
            frames(1);
            expect(result.current).toBeGreaterThanOrEqual(previous);
            previous = result.current;
        }
        expect(result.current).toBe(200);
        expect(vi.getTimerCount()).toBe(0);
    });

    it('schedules nothing when a new float target rounds to the shown value', () => {
        const { result, rerender } = counter(100);
        frames(25);
        const shown = result.current;

        // Both sides: ceil rounds +0.4 away from the shown value, floor and
        // trunc round -0.4 away.
        rerender({ target: shown + 0.4, duration: undefined });
        expect(vi.getTimerCount()).toBe(0);
        rerender({ target: shown - 0.4, duration: undefined });
        expect(vi.getTimerCount()).toBe(0);

        frames(FULL_RUN);
        expect(result.current).toBe(shown);
    });

    it('schedules nothing for a target of 0 on mount', () => {
        const { result } = counter(0);
        expect(result.current).toBe(0);
        expect(vi.getTimerCount()).toBe(0);
    });

    it('animates a drop to 0 down from the shown value', () => {
        const { result, rerender } = counter(100);
        frames(13);
        const shown = result.current;
        expect(shown).toBeGreaterThan(0);

        rerender({ target: 0, duration: undefined });
        expect(result.current).toBe(shown);   // no snap

        frames(2);   // the clock-starting frame, then one step
        expect(result.current).toBeGreaterThan(0);
        expect(result.current).toBeLessThan(shown);

        frames(FULL_RUN);
        expect(result.current).toBe(0);
        expect(vi.getTimerCount()).toBe(0);
    });

    it('lands on the target at the first frame when the duration is 0', () => {
        const { result } = counter(42, 0);
        frames(1);
        expect(result.current).toBe(42);
        expect(vi.getTimerCount()).toBe(0);
    });

    it('does not restart when a re-render keeps the same target', () => {
        // StatsGrid re-renders all four counters when the ticket count lands,
        // and a dashboard refresh hands over a new stats object with the same
        // numbers. Neither may restart a counter that is mid-run.
        const control = counter(100);
        frames(30);
        const uninterrupted = control.result.current;
        control.unmount();

        const { result, rerender } = counter(100);
        frames(15);
        rerender({ target: 100, duration: undefined });
        frames(15);
        expect(result.current).toBe(uninterrupted);
    });

    it('cancels and schedules nothing when the target flips back before the first frame', () => {
        const { result, rerender } = counter(100);
        expect(vi.getTimerCount()).toBe(1);

        rerender({ target: 0, duration: undefined });
        expect(vi.getTimerCount()).toBe(0);
        frames(FULL_RUN);
        expect(result.current).toBe(0);
    });

    it.each([
        ['NaN', NaN],
        ['Infinity', Infinity],
        // What App.test's blanket `{ data: [] }` stub hands StatsGrid.
        ['undefined', undefined as unknown as number],
    ])('treats a target of %s as 0 and recovers on the next real one', (_label, target) => {
        const { result, rerender } = counter(target);
        expect(result.current).toBe(0);
        expect(vi.getTimerCount()).toBe(0);

        rerender({ target: 10, duration: undefined });
        frames(FULL_RUN);
        expect(result.current).toBe(10);
    });
});

describe('useAnimatedCounter, on frame timestamps the test chooses', () => {
    // A browser stamps a frame with the time the frame began, which can be
    // earlier than code that ran just before it, and a background tab delivers
    // its next frame long after the duration. Fake timers put every frame on a
    // 16 ms grid, so these cases use a hand-driven queue instead.
    let pending: Map<number, FrameRequestCallback>;
    let lastId: number;

    beforeEach(() => {
        pending = new Map();
        lastId = 0;
        vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
            pending.set(++lastId, cb);
            return lastId;
        });
        vi.stubGlobal('cancelAnimationFrame', (id: number) => { pending.delete(id); });
    });

    afterEach(() => {
        vi.unstubAllGlobals();
    });

    function frameAt(timestamp: number) {
        const due = [...pending.values()];
        pending.clear();
        act(() => { due.forEach(cb => cb(timestamp)); });
    }

    it('never drops below the start for a frame stamped before the effect ran', () => {
        // Floored: with a fractional base, (base + 1200) - base can come out a
        // hair under 1200, and the last frame then asks for one more.
        const before = Math.floor(performance.now());
        const { result } = renderHook(() => useAnimatedCounter(100));

        frameAt(before - 50);
        expect(result.current).toBe(0);

        frameAt(before - 50 + 600);   // halfway
        expect(result.current).toBeGreaterThan(0);
        expect(result.current).toBeLessThan(100);

        frameAt(before - 50 + 1200);
        expect(result.current).toBe(100);
        expect(pending.size).toBe(0);
    });

    it('lands exactly on the target when the next frame comes long after the duration', () => {
        const { result } = renderHook(() => useAnimatedCounter(100));
        frameAt(1000);          // starts the clock
        frameAt(1100);
        frameAt(1000 + 3000);   // a background tab comes back
        expect(result.current).toBe(100);
        expect(pending.size).toBe(0);
    });
});
