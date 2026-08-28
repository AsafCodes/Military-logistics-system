/**
 * SEC-H9: both HTTP clients must send the cookie and attach no token.
 *
 * There are two live axios instances in this tree (FE-H1 tracks that as its own
 * defect). Both had a request interceptor reading localStorage['token']. Both
 * are covered here, because a fix applied to one and not the other is exactly
 * the shape of bug this codebase keeps producing.
 *
 * Everything below drives the clients through their PUBLIC api with a stubbed
 * adapter, rather than reaching into `client.interceptors.*.handlers`. That
 * private array is not in axios's typed surface, so a rename in a minor bump
 * would leave the harness iterating nothing and every assertion here passing
 * vacuously -- the failure mode a regression guard can least afford.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import axios, { AxiosError, type AxiosAdapter, type InternalAxiosRequestConfig } from 'axios';
import apiClient from './axios';
import api from '@/api';

const CLIENTS: Array<[string, typeof api]> = [
    ['lib/axios (auth + services)', apiClient],
    ['api.ts (feature pages)', api],
];

/** Swap in an adapter, restoring whatever the client had, per test. */
function withAdapter(client: typeof api) {
    const original = client.defaults.adapter;
    const restore = () => { client.defaults.adapter = original; };
    const install = (adapter: AxiosAdapter) => { client.defaults.adapter = adapter; };
    return { install, restore };
}

describe.each(CLIENTS)('%s', (_name, client) => {
    let adapter: ReturnType<typeof withAdapter>;

    beforeEach(() => { adapter = withAdapter(client); });
    afterEach(() => { adapter.restore(); });

    it('sends credentials, so the browser attaches the httpOnly cookie', () => {
        // The frontend is served from :3000 and the API from :8000. Without
        // this the browser withholds the cookie on every cross-origin call and
        // the whole application 401s.
        expect(client.defaults.withCredentials).toBe(true);
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

        await client.get('/users/me');

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

        await client.get('/users/me');

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

        await client.post('/equipment/', { serial_number: 'X1' });

        expect(String(sent!.headers['Content-Type'])).toContain('application/json');
    });

    it('leaves the stale token alone rather than pretending to have handled it', async () => {
        // Scrubbing belongs in main.tsx, once, at startup. A client that also
        // cleared it would be a second owner of the same decision.
        localStorage.setItem('token', 'LEFTOVER');
        adapter.install(async config => ({
            data: {}, status: 200, statusText: 'OK', headers: {}, config,
        }));

        await client.get('/users/me');

        expect(localStorage.getItem('token')).toBe('LEFTOVER');
    });
});

describe('lib/axios 401 handling', () => {
    const realLocation = Object.getOwnPropertyDescriptor(window, 'location');
    let href: string;
    let adapter: ReturnType<typeof withAdapter>;

    function stubLocation(pathname: string) {
        href = `http://localhost:3000${pathname}`;
        Object.defineProperty(window, 'location', {
            configurable: true,
            value: {
                pathname,
                get href() { return href; },
                set href(value: string) { href = value; },
                reload: vi.fn(),
            },
        });
    }

    /** Make the next request fail with a genuine AxiosError of `status`. */
    function respondWith(status: number) {
        adapter.install(async config => {
            throw new AxiosError(
                `Request failed with status code ${status}`,
                AxiosError.ERR_BAD_REQUEST,
                config,
                {},
                { data: {}, status, statusText: '', headers: {}, config },
            );
        });
    }

    beforeEach(() => { adapter = withAdapter(apiClient); });

    afterEach(() => {
        adapter.restore();
        if (realLocation) Object.defineProperty(window, 'location', realLocation);
    });

    it('redirects to login on an unexpected 401', async () => {
        stubLocation('/dashboard');
        respondWith(401);

        await expect(apiClient.get('/equipment/accessible')).rejects.toBeInstanceOf(AxiosError);
        expect(window.location.href).toBe('/login');
    });

    it('does NOT redirect when the caller opted out', async () => {
        // The cold-load session probe. Without this branch every anonymous
        // visit becomes a full-page navigation to the page already displayed.
        stubLocation('/dashboard');
        respondWith(401);

        await expect(
            apiClient.get('/users/me', { skipAuthRedirect: true }),
        ).rejects.toBeInstanceOf(AxiosError);
        expect(window.location.href).toBe('http://localhost:3000/dashboard');
    });

    it('does not redirect when already on the login page', async () => {
        // Pre-existing guard against a redirect loop; kept working.
        stubLocation('/login');
        respondWith(401);

        await expect(apiClient.get('/users/me')).rejects.toBeInstanceOf(AxiosError);
        expect(window.location.href).toBe('http://localhost:3000/login');
    });

    it('leaves non-401 failures alone', async () => {
        stubLocation('/dashboard');
        respondWith(500);

        await expect(apiClient.get('/users/me')).rejects.toBeInstanceOf(AxiosError);
        expect(window.location.href).toBe('http://localhost:3000/dashboard');
    });

    it('still rejects, so callers can handle the error', async () => {
        // An interceptor that swallowed the rejection would make
        // resolveSession resolve with undefined instead of taking its catch.
        stubLocation('/dashboard');
        respondWith(401);

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
