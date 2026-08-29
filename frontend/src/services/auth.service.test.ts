/**
 * SEC-H9: the auth service must store nothing.
 *
 * The headline assertion in this file is `localStorage.length === 0`. It reads
 * like a triviality and it is the whole ticket: the defect was a bearer token
 * for a military logistics system sitting in web storage where any script on
 * the page -- an XSS, a compromised dependency -- could read and exfiltrate it.
 * Any future change that reintroduces a cache here fails these tests.
 */
import { describe, it, expect, vi } from 'vitest';
import apiClient from '@/lib/axios';
import { authService } from './auth.service';
import { TEST_USER as USER, TEST_CAPABILITIES as CAPS } from '@/test/setup';

// Both calls in resolveSession() hit the same client method (apiClient.get),
// so a mock keyed only on method would answer identically for /users/me and
// /users/me/capabilities. Keyed on URL instead, matching what the two really
// are: two different endpoints, not one call answered twice.
function mockBothEndpoints(userResult: 'resolve' | 'reject', capsResult: 'resolve' | 'reject') {
    vi.spyOn(apiClient, 'get').mockImplementation((url: string) => {
        if (url === '/users/me') {
            return userResult === 'resolve'
                ? Promise.resolve({ data: USER })
                : Promise.reject({ response: { status: 401 } });
        }
        if (url === '/users/me/capabilities') {
            return capsResult === 'resolve'
                ? Promise.resolve({ data: CAPS })
                : Promise.reject(new Error('500'));
        }
        throw new Error(`unexpected URL in test: ${url}`);
    });
}

// Spies are restored centrally via `restoreMocks` in vite.config.ts.
describe('authService', () => {
    describe('stores nothing in web storage', () => {
        it('login writes nothing', async () => {
            vi.spyOn(apiClient, 'post').mockResolvedValue({
                data: { access_token: 'a-real-looking-token', token_type: 'bearer' },
            });

            await authService.login({ personalNumber: 'u_master', password: 'secret' });

            expect(localStorage.length).toBe(0);
            expect(sessionStorage.length).toBe(0);
        });

        it('login ignores the token in the response body', async () => {
            // The body still carries a token -- non-browser clients need it.
            // The point is that this code never touches it.
            vi.spyOn(apiClient, 'post').mockResolvedValue({
                data: { access_token: 'SHOULD_NOT_BE_STORED', token_type: 'bearer' },
            });

            await authService.login({ personalNumber: 'u_master', password: 'secret' });

            expect(JSON.stringify(localStorage)).not.toContain('SHOULD_NOT_BE_STORED');
        });

        it('resolveSession writes nothing', async () => {
            mockBothEndpoints('resolve', 'resolve');

            await authService.resolveSession();

            expect(localStorage.length).toBe(0);
        });
    });

    describe('login', () => {
        it('posts form-urlencoded credentials, as OAuth2PasswordRequestForm requires', async () => {
            const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {} });

            await authService.login({ personalNumber: 'u_master', password: 'secret' });

            const [url, body, config] = post.mock.calls[0];
            expect(url).toBe('/login');
            expect(config?.headers?.['Content-Type']).toBe('application/x-www-form-urlencoded');

            // Field names are `username`/`password`, NOT `personalNumber`.
            // Renaming them here is a silent 422 from the backend.
            const params = body as URLSearchParams;
            expect(params.get('username')).toBe('u_master');
            expect(params.get('password')).toBe('secret');
        });
    });

    describe('logout', () => {
        it('calls the server, because only the server can clear an httpOnly cookie', async () => {
            const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: null });

            await authService.logout();

            expect(post).toHaveBeenCalledWith('/logout');
        });

        it('propagates a failure rather than reporting a logout that did not happen', async () => {
            vi.spyOn(apiClient, 'post').mockRejectedValue(new Error('network down'));

            // What matters is that this method does not swallow the failure and
            // report a logout that did not happen. App.tsx deliberately does
            // NOT clear local state on this rejection -- see its handleLogout,
            // and the "does NOT fake a logout" test in App.test.tsx. Rendering
            // a logged-out UI over a live cookie is the shared-terminal hazard
            // the whole path is arranged to avoid.
            await expect(authService.logout()).rejects.toThrow('network down');
        });
    });

    describe('resolveSession', () => {
        it('returns the user and their capabilities when the server recognises the cookie', async () => {
            mockBothEndpoints('resolve', 'resolve');

            await expect(authService.resolveSession()).resolves.toEqual({
                user: USER,
                capabilities: CAPS,
            });
        });

        it('returns null instead of throwing when nobody is signed in', async () => {
            // Both endpoints refuse an unrecognised cookie the same way; only
            // /users/me needs to for this case, but a real 401 would refuse both.
            mockBothEndpoints('reject', 'reject');

            // On a first visit a 401 is the ordinary answer, not an error. This
            // returning null rather than throwing is what lets App.tsx's mount
            // effect finish and clear the loading spinner.
            await expect(authService.resolveSession()).resolves.toBeNull();
        });

        it('SEC-H10: throws -- does not report "signed out" -- when the cookie is recognised but permissions fail to load', async () => {
            // The cookie IS valid (userResult resolves); only the capabilities
            // call fails. Silently returning null here would tell a genuinely
            // signed-in operator they are logged out, which is worse than an
            // error: it is a believable lie.
            mockBothEndpoints('resolve', 'reject');

            await expect(authService.resolveSession()).rejects.toThrow();
        });

        it('opts out of the global 401 redirect on both calls', async () => {
            mockBothEndpoints('resolve', 'resolve');
            const get = vi.spyOn(apiClient, 'get');

            await authService.resolveSession();

            // Without this flag the interceptor turns every anonymous visit
            // into a full-page navigation to a page already being displayed.
            for (const call of get.mock.calls) {
                expect(call[1]).toMatchObject({ skipAuthRedirect: true });
            }
        });
    });

    describe('the removed API', () => {
        it('exposes no way to read a token', () => {
            // getToken/getCachedUser/isAuthenticated are gone by design. A
            // caller that can ask for the token is a caller that can store it.
            const service = authService as unknown as Record<string, unknown>;
            expect(service.getToken).toBeUndefined();
            expect(service.getCachedUser).toBeUndefined();
            expect(service.isAuthenticated).toBeUndefined();
        });

        it('offers exactly one way to ask who is signed in', () => {
            // getMe was a second, throwing spelling of resolveSession. Two of
            // them meant every caller picked a failure mode, and the login path
            // picked the one that reports success as failure.
            const service = authService as unknown as Record<string, unknown>;
            expect(service.getMe).toBeUndefined();
            expect(typeof service.resolveSession).toBe('function');
        });
    });
});
