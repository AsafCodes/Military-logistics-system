/**
 * The boot path itself.
 *
 * main.tsx runs code at MODULE SCOPE, before createRoot().render(). Anything
 * that throws there takes the whole application down in the one way nothing can
 * recover from: React never mounts, so the ErrorBoundary added by this same
 * ticket is not in the tree yet and cannot catch it. The user gets a blank white
 * page -- which is precisely the failure SEC-H9 exists to eliminate.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, waitFor } from '@testing-library/react';

vi.mock('@/components/ui/NetworkGlobe', () => ({ default: () => null }));
vi.mock('./components/shared/ConnectionTest', () => ({ default: () => null }));
// Plain functions, deliberately not vi.fn(): `restoreMocks` in vite.config.ts
// resets implementations between tests, which would strip the mockResolvedValue
// off a spy declared in a module factory -- from the FIRST test onward, not
// just subsequent ones -- handing App an undefined return where it expects a
// promise. Nothing here asserts on these calls, so there is nothing to gain
// from spies.
vi.mock('./services', () => ({
    authService: {
        resolveSession: () => Promise.resolve(null),
        login: () => Promise.resolve(),
        logout: () => Promise.resolve(),
    },
}));

const realLocalStorage = Object.getOwnPropertyDescriptor(window, 'localStorage');

/** A browser that refuses site data: touching localStorage throws. */
function denyStorageAccess() {
    Object.defineProperty(window, 'localStorage', {
        configurable: true,
        get() {
            throw new DOMException('The operation is insecure.', 'SecurityError');
        },
    });
}

function restoreStorage() {
    if (realLocalStorage) {
        Object.defineProperty(window, 'localStorage', realLocalStorage);
    }
}

describe('application boot', () => {
    beforeEach(() => {
        vi.resetModules();
        document.body.innerHTML = '<div id="root"></div>';
    });

    afterEach(() => {
        restoreStorage();
        document.body.innerHTML = '';
    });

    // createRoot().render() schedules work rather than mounting synchronously,
    // hence waitFor rather than a bare assertion.
    //
    // Asserting on the ERROR BOUNDARY, not merely on children.length: the
    // fallback is itself a rendered child, so a "did anything mount" check
    // passes just as happily on the crash screen as on the application. That
    // weaker assertion hid a real defect through a whole review cycle.
    const mounted = () =>
        waitFor(() => {
            const root = document.getElementById('root')!;
            expect(root.children.length).toBeGreaterThan(0);
            expect(root.textContent).not.toMatch(/something went wrong/i);
        });

    it('mounts when storage is available', async () => {
        await act(async () => { await import('./main'); });
        await mounted();
    });

    it('mounts even when the browser denies storage access', async () => {
        // Real conditions, not exotic: site data blocked by policy or by the
        // user, a sandboxed iframe, some private-browsing modes. The ACCESSOR
        // throws -- a try/catch around JSON.parse would not have helped.
        denyStorageAccess();

        await act(async () => {
            await expect(import('./main')).resolves.toBeDefined();
        });
        await mounted();
    });

    it('still scrubs the legacy keys when storage does work', async () => {
        restoreStorage();
        localStorage.setItem('token', 'LEFTOVER_PRE_SEC_H9_TOKEN');
        localStorage.setItem('user', '{"id":1}');
        localStorage.setItem('theme', 'dark');

        await act(async () => { await import('./main'); });

        expect(localStorage.getItem('token')).toBeNull();
        expect(localStorage.getItem('user')).toBeNull();
        // Unrelated keys are not ours to delete.
        expect(localStorage.getItem('theme')).toBe('dark');
    });
});
