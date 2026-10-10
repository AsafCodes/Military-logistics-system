import { useState, useEffect, useRef } from 'react';

/**
 * Eases a displayed integer towards `target` over `duration` ms (FE-H4).
 *
 * Each run starts from the value already on screen, not from 0, so a new
 * target continues the count instead of rewinding it. The effect's cleanup
 * cancels the pending frame, so an unmount or a retarget mid-animation leaves
 * no loop behind. The clock starts at the first frame's timestamp: a frame
 * timestamp can be earlier than a performance.now() read in the effect, which
 * would make progress negative.
 *
 * A non-finite target (an undefined field, say) counts to 0, so it cannot
 * leave NaN as the starting point for every later run.
 */
export function useAnimatedCounter(target: number, duration = 1200) {
    const [count, setCount] = useState(0);
    // The last value handed to setCount: where the next run starts from.
    const shown = useRef(0);

    useEffect(() => {
        const from = shown.current;
        const to = Number.isFinite(target) ? Math.round(target) : 0;
        if (from === to) return;

        let start: number | undefined;

        const tick = (now: number) => {
            // Not `??=`: the React Compiler bails out on it, which silently
            // switches off the react-hooks rules that run on its analysis
            // (set-state-in-effect among them) for this whole hook.
            if (start === undefined) start = now;
            const progress = duration > 0 ? Math.min((now - start) / duration, 1) : 1;
            // Ease-out cubic
            const eased = 1 - Math.pow(1 - progress, 3);
            const current = Math.round(from + (to - from) * eased);

            if (current !== shown.current) {
                shown.current = current;
                setCount(current);
            }

            if (progress < 1) {
                frame = requestAnimationFrame(tick);
            }
        };

        let frame = requestAnimationFrame(tick);
        // Reads `frame` when it runs, so it cancels the latest frame, not the first.
        return () => cancelAnimationFrame(frame);
    }, [target, duration]);

    return count;
}
