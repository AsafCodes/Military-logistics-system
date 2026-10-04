/**
 * Shared stubs for driving the HTTP client through its PUBLIC api.
 *
 * Tests swap the client's adapter rather than reaching into
 * `client.interceptors.*.handlers`. That array is undocumented and typed only
 * as optional (`handlers?`), so a harness has to tolerate its absence -- and
 * if axios ever stopped filling it, such a harness would iterate nothing and
 * every assertion would pass vacuously. Going through the adapter means a
 * request runs the real interceptors, exactly as a page's request does.
 */
import { vi } from 'vitest';
import { AxiosError, type AxiosAdapter, type AxiosInstance } from 'axios';

/** Swap in an adapter, restoring whatever the client had. */
export function withAdapter(client: AxiosInstance) {
    const original = client.defaults.adapter;
    const restore = () => { client.defaults.adapter = original; };
    const install = (adapter: AxiosAdapter) => { client.defaults.adapter = adapter; };
    return { install, restore };
}

/** An adapter whose every request fails with a genuine AxiosError of `status`. */
export function failingWith(status: number): AxiosAdapter {
    return async config => {
        throw new AxiosError(
            `Request failed with status code ${status}`,
            AxiosError.ERR_BAD_REQUEST,
            config,
            {},
            { data: {}, status, statusText: '', headers: {}, config },
        );
    };
}

const REAL_LOCATION = Object.getOwnPropertyDescriptor(window, 'location');

/**
 * Replace window.location with one sitting at `pathname`, whose `href` can be
 * assigned and read back, and whose `reload` is a spy. jsdom implements
 * neither navigation nor reload, so the real object can't show which of the
 * two the client chose. Undo it with `restoreLocation()`.
 */
export function stubLocation(pathname: string) {
    let href = `http://localhost:3000${pathname}`;
    const reload = vi.fn();
    Object.defineProperty(window, 'location', {
        configurable: true,
        value: {
            pathname,
            get href() { return href; },
            set href(value: string) { href = value; },
            reload,
        },
    });
    return { reload };
}

export function restoreLocation() {
    if (REAL_LOCATION) Object.defineProperty(window, 'location', REAL_LOCATION);
}
