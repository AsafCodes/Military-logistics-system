/**
 * Shared stubs for driving the HTTP client through its PUBLIC api.
 *
 * Tests swap the client's adapter rather than reaching into
 * `client.interceptors.*.handlers`. That array is undocumented and typed only
 * as optional (`handlers?`), so a harness has to tolerate its absence -- and
 * if axios ever stopped filling it, such a harness would iterate nothing and
 * every assertion would pass vacuously. Going through the adapter means a
 * request runs the real interceptors, exactly as a page's request does.
 *
 * `holdGets` and `holdPosts` are the exceptions: spies on `get` and `post`,
 * for tests that only need to control when, and in what order, responses
 * land.
 */
import { vi } from 'vitest';
import { AxiosError, type AxiosAdapter, type AxiosInstance, type AxiosRequestConfig } from 'axios';

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

/** A GET that `holdGets` is holding open until the test settles it. */
export interface HeldGet {
    url: string;
    config: AxiosRequestConfig | undefined;
    resolve: (data: unknown) => void;
    reject: (reason: unknown) => void;
}

/**
 * Spy on `client.get`. A URL that is a key of `answers` resolves at once with
 * that data. Every other call is held: pushed onto the returned `held` array
 * and left unsettled until the test calls its `resolve` or `reject`, so a test
 * can settle requests in any order it likes.
 *
 * A held call does NOT reject by itself when its signal aborts, although the
 * real client would: axios rejects with a CanceledError on abort, and also
 * turns a response that resolves after the abort into one. A test models that
 * with `reject(new CanceledError())`, or resolves after the abort to check that
 * the component's own post-await check drops the result.
 *
 * This is a spy, so the client's interceptors do not run; use `withAdapter`
 * for anything that depends on them.
 */
export function holdGets(client: AxiosInstance, answers: Record<string, unknown> = {}) {
    const held: HeldGet[] = [];
    const spy = vi.spyOn(client, 'get').mockImplementation(
        (url: string, config?: AxiosRequestConfig) => {
            if (Object.hasOwn(answers, url)) return Promise.resolve({ data: answers[url] });
            return new Promise((resolve, reject) => {
                held.push({ url, config, resolve: data => resolve({ data }), reject });
            });
        },
    );
    return { held, spy };
}

/** A POST that `holdPosts` is holding open until the test settles it. */
export interface HeldPost {
    url: string;
    body: unknown;
    resolve: (data?: unknown) => void;
    reject: (reason: unknown) => void;
}

/**
 * Spy on `client.post` and hold every call, so a test can look at a form
 * while its write is out and then make the write succeed or fail. A spy, like
 * `holdGets`: the client's interceptors do not run.
 */
export function holdPosts(client: AxiosInstance) {
    const held: HeldPost[] = [];
    vi.spyOn(client, 'post').mockImplementation(
        (url: string, body?: unknown) => new Promise((resolve, reject) => {
            held.push({ url, body, resolve: data => resolve({ data }), reject });
        }),
    );
    return held;
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
