/**
 * The application's only HTTP client (FE-H1; singleHttpClient.test.ts pins
 * that it stays the only one).
 *
 * Until FE-H1 there were two axios instances with different timeouts and
 * different 401 policies -- api.ts reloaded the page, this one navigates to
 * /login -- and SEC-H9 had to fix the stale-token interceptor in both. One
 * client means one place for each of the settings pinned below.
 *
 * Everything here drives the client through its PUBLIC api with a stubbed
 * adapter; see src/test/httpStubs.ts for why.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios';
import apiClient from './axios';
import { withAdapter, failingWith, stubLocation, restoreLocation } from '@/test/httpStubs';

describe('lib/axios configuration', () => {
    let adapter: ReturnType<typeof withAdapter>;

    beforeEach(() => { adapter = withAdapter(apiClient); });
    afterEach(() => { adapter.restore(); });

    it('sends credentials, so the browser attaches the httpOnly cookie', () => {
        // The frontend is served from :3000 and the API from :8000. Without
        // this the browser withholds the cookie on every cross-origin call and
        // the whole application 401s.
        expect(apiClient.defaults.withCredentials).toBe(true);
    });

    it('gives up after ten seconds', () => {
        // Every feature page used to go through a client with no timeout at
        // all, so a hung request left its spinner up forever.
        expect(apiClient.defaults.timeout).toBe(10000);
    });

    describe('base address', () => {
        // The module reads the variable once, at import, so each case needs a
        // fresh copy of it.
        afterEach(() => {
            vi.unstubAllEnvs();
            vi.resetModules();
        });

        it('comes from VITE_API_URL', async () => {
            vi.stubEnv('VITE_API_URL', 'https://api.example.test');
            vi.resetModules();
            const { default: fresh } = await import('./axios');
            expect(fresh.defaults.baseURL).toBe('https://api.example.test');
        });

        // Empty as well as unset: a key left present but blank must fall back
        // too, and `??` in place of `||` would hand axios '' instead.
        it.each([
            ['unset', undefined],
            ['empty', ''],
        ])('falls back to the local API when VITE_API_URL is %s', async (_label, value) => {
            vi.stubEnv('VITE_API_URL', value);
            vi.resetModules();
            const { default: fresh } = await import('./axios');
            expect(fresh.defaults.baseURL).toBe('http://localhost:8000');
        });
    });

    it('attaches no Authorization header even when a stale token is present', async () => {
        // The hostile case, and the reason main.tsx scrubs these keys: a
        // browser upgraded from the pre-fix build still has a live bearer token
        // in localStorage. It must be inert -- read by nothing, sent nowhere.
        localStorage.setItem('token', 'LEFTOVER_PRE_SEC_H9_TOKEN');

        let sent: InternalAxiosRequestConfig | undefined;
        adapter.install(async config => {
            sent = config;
            return { data: {}, status: 200, statusText: 'OK', headers: {}, config };
        });

        await apiClient.get('/users/me');

        expect(sent!.headers.Authorization).toBeUndefined();
        expect(JSON.stringify(sent!.headers)).not.toContain('LEFTOVER_PRE_SEC_H9_TOKEN');
    });

    it('sends no Content-Type on a bodyless GET, so it stays CORS-simple', async () => {
        // application/json is not a CORS-safelisted Content-Type, so setting it
        // as a client default makes every cross-origin GET pay an OPTIONS
        // preflight first. That doubles the cold-load session probe, which is
        // now the thing standing between the user and first paint.
        let sent: InternalAxiosRequestConfig | undefined;
        adapter.install(async config => {
            sent = config;
            return { data: {}, status: 200, statusText: 'OK', headers: {}, config };
        });

        await apiClient.get('/users/me');

        expect(sent!.headers['Content-Type']).toBeUndefined();
    });

    it('still sets Content-Type when there IS a body to describe', async () => {
        // The flip side: removing the default must not stop axios labelling
        // real JSON payloads, or every write would reach FastAPI unparseable.
        let sent: InternalAxiosRequestConfig | undefined;
        adapter.install(async config => {
            sent = config;
            return { data: {}, status: 200, statusText: 'OK', headers: {}, config };
        });

        await apiClient.post('/equipment/', { serial_number: 'X1' });

        expect(String(sent!.headers['Content-Type'])).toContain('application/json');
    });

    it('leaves the stale token alone rather than pretending to have handled it', async () => {
        // Scrubbing belongs in main.tsx, once, at startup. A client that also
        // cleared it would be a second owner of the same decision.
        localStorage.setItem('token', 'LEFTOVER');
        adapter.install(async config => ({
            data: {}, status: 200, statusText: 'OK', headers: {}, config,
        }));

        await apiClient.get('/users/me');

        expect(localStorage.getItem('token')).toBe('LEFTOVER');
    });
});

describe('lib/axios 401 handling', () => {
    let adapter: ReturnType<typeof withAdapter>;

    beforeEach(() => { adapter = withAdapter(apiClient); });

    afterEach(() => {
        adapter.restore();
        restoreLocation();
    });

    it('redirects to login on an unexpected 401, without reloading', async () => {
        // Navigating, not reloading: a reload re-requests the page that just
        // 401'd, and the old api.ts client did exactly that.
        const { reload } = stubLocation('/dashboard');
        adapter.install(failingWith(401));

        await expect(apiClient.get('/equipment/accessible')).rejects.toBeInstanceOf(AxiosError);
        expect(window.location.href).toBe('/login');
        expect(reload).not.toHaveBeenCalled();
    });

    it('does NOT redirect when the caller opted out', async () => {
        // The cold-load session probe. Without this branch every anonymous
        // visit becomes a full-page navigation to the page already displayed.
        stubLocation('/dashboard');
        adapter.install(failingWith(401));

        await expect(
            apiClient.get('/users/me', { skipAuthRedirect: true }),
        ).rejects.toBeInstanceOf(AxiosError);
        expect(window.location.href).toBe('http://localhost:3000/dashboard');
    });

    it('does not redirect when already on the login page', async () => {
        // Pre-existing guard against a redirect loop; kept working.
        stubLocation('/login');
        adapter.install(failingWith(401));

        await expect(apiClient.get('/users/me')).rejects.toBeInstanceOf(AxiosError);
        expect(window.location.href).toBe('http://localhost:3000/login');
    });

    it('leaves non-401 failures alone', async () => {
        stubLocation('/dashboard');
        adapter.install(failingWith(500));

        await expect(apiClient.get('/users/me')).rejects.toBeInstanceOf(AxiosError);
        expect(window.location.href).toBe('http://localhost:3000/dashboard');
    });

    it('still rejects, so callers can handle the error', async () => {
        // An interceptor that swallowed the rejection would make
        // resolveSession resolve with undefined instead of taking its catch.
        stubLocation('/dashboard');
        adapter.install(failingWith(401));

        await expect(
            apiClient.get('/users/me', { skipAuthRedirect: true }),
        ).rejects.toMatchObject({ response: { status: 401 } });
    });

    it('is reached through the real axios error path', () => {
        // Guards the harness itself: if AxiosError ever stopped satisfying
        // isAxiosError, every assertion above would pass for the wrong reason.
        const error = new AxiosError('x', AxiosError.ERR_BAD_REQUEST);
        expect(axios.isAxiosError(error)).toBe(true);
    });
});

/**
 * FE-H5. The pages cancel reads with an AbortSignal. The adapter here answers
 * only after the test aborts, and axios turns that answer, success or 401,
 * into a CanceledError, so a page's catch guard sees it and its post-await
 * check is never reached. The browser's own transport rejects on abort by
 * itself.
 */
describe('lib/axios cancellation (FE-H5)', () => {
    let adapter: ReturnType<typeof withAdapter>;

    beforeEach(() => { adapter = withAdapter(apiClient); });

    afterEach(() => {
        adapter.restore();
        restoreLocation();
    });

    // An adapter whose single request is settled by the test.
    function deferredAdapter() {
        let settle: { resolve: (status: number) => void } | undefined;
        adapter.install(config => new Promise((resolve, reject) => {
            settle = {
                resolve: status => {
                    const response = { data: {}, status, statusText: '', headers: {}, config };
                    if (status < 400) resolve(response);
                    else reject(new AxiosError('failed', AxiosError.ERR_BAD_REQUEST, config, {}, response));
                },
            };
        }));
        return (status: number) => {
            if (!settle) throw new Error('the request never reached the adapter');
            settle.resolve(status);
        };
    }

    it('turns a response that lands after the abort into a CanceledError', async () => {
        const answer = deferredAdapter();
        const controller = new AbortController();

        const request = apiClient.get('/reports/daily_movement', { signal: controller.signal });
        controller.abort();
        answer(200);

        await expect(request).rejects.toSatisfy(axios.isCancel);
    });

    it('does not navigate to /login for a 401 that lands after the abort', async () => {
        // The interceptor acts only on an error carrying a 401 response. A
        // CanceledError carries none, so a request whose signal aborted
        // before its 401 landed does not redirect.
        const { reload } = stubLocation('/dashboard');
        const answer = deferredAdapter();
        const controller = new AbortController();

        const request = apiClient.get('/equipment/accessible', { signal: controller.signal });
        controller.abort();
        answer(401);

        await expect(request).rejects.toSatisfy(axios.isCancel);
        expect(window.location.href).toBe('http://localhost:3000/dashboard');
        expect(reload).not.toHaveBeenCalled();
    });

    it('sends nothing for a signal that aborted before the request', async () => {
        // useLatestRequest hands out such a signal once its component is gone.
        const reached = vi.fn();
        adapter.install(async config => {
            reached();
            return { data: {}, status: 200, statusText: '', headers: {}, config };
        });
        const controller = new AbortController();
        controller.abort();

        await expect(apiClient.get('/tickets/', { signal: controller.signal })).rejects.toSatisfy(axios.isCancel);
        expect(reached).not.toHaveBeenCalled();
    });

    it('still navigates for a 401 when nothing aborted (control)', async () => {
        stubLocation('/dashboard');
        const answer = deferredAdapter();

        const request = apiClient.get('/equipment/accessible', { signal: new AbortController().signal });
        answer(401);

        // A CanceledError is an AxiosError too; the response tells them apart.
        await expect(request).rejects.toMatchObject({ response: { status: 401 } });
        expect(window.location.href).toBe('/login');
    });
});
