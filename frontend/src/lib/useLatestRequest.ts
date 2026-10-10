import { useCallback, useEffect, useRef } from 'react';

/**
 * Latest-wins cancellation for a load that more than one caller can start
 * (FE-H5): the mount effect, a Refresh or Retry button, a reload after a
 * write.
 *
 * Each `next()` aborts the signal the previous call handed out and returns a
 * fresh one, so only the most recent load can still land. Unmounting aborts
 * whichever signal is current, and a `next()` called after that returns a
 * signal that is already aborted: a write still out when the page is left
 * calls its reload afterwards, and the client sends nothing for such a
 * signal. `next` keeps one identity for the component's lifetime, so a load
 * wrapped in `useCallback([next])` stays stable too.
 *
 * A load passes the signal to every read it sends and checks
 * `signal.aborted` after its await and first thing in its catch, so a
 * superseded load neither writes state nor reports its abort as a failure.
 * Writes never take a signal: aborting one can't undo a change the server
 * has already committed.
 */
export function useLatestRequest(): () => AbortSignal {
    const current = useRef<AbortController | null>(null);
    const unmounted = useRef(false);

    // The setup resets the flag because StrictMode runs the cleanup once on
    // a component that then stays mounted.
    useEffect(() => {
        unmounted.current = false;
        return () => {
            unmounted.current = true;
            current.current?.abort();
        };
    }, []);

    return useCallback(() => {
        current.current?.abort();
        const controller = new AbortController();
        if (unmounted.current) controller.abort();
        current.current = controller;
        return controller.signal;
    }, []);
}
