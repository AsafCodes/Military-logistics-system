/**
 * SEC-H9: the application must survive a hostile localStorage and a refused
 * session, and must always stop showing the loading spinner.
 *
 * The bug being pinned here: App's mount effect read a cached user out of
 * localStorage and JSON.parse'd it with no guard, THEN called setIsLoading
 * (false) on the following line. One malformed character threw before the
 * spinner could clear, and with no error boundary anywhere the operator got a
 * permanently blank page. Not a transient error -- a dead application, until
 * someone thought to clear their browser storage.
 */
import { StrictMode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import App from './App';
import { authService } from './services';
import type { Session } from './types';
import {
    TEST_SESSION as SESSION,
    TEST_CAPABILITIES_NONE as NO_CAPS,
} from './test/setup';

// A granted admin's session, and the ungranted equivalent -- SEC-H10's route
// guard reads system: ['MANAGE_PERSONNEL'] from the second, so the two must
// differ only in capabilities, never in identity, to isolate what the guard
// is actually keying on.
const SESSION_NO_ADMIN = { ...SESSION, capabilities: NO_CAPS };

// The login page renders a WebGL globe. jsdom has no WebGL context, so without
// this the three.js canvas throws and every assertion below fails for a reason
// that has nothing to do with authentication.
vi.mock('@/components/ui/NetworkGlobe', () => ({
    default: () => null,
}));

// The dashboard loads its own data on mount through the shared client. These
// tests are about the session bootstrap, not that data, and letting the
// requests fly produces real network errors in the output.
//
// authService goes through this same client, so the mock answers the session
// probe's signature (`skipAuthRedirect`) the way an anonymous visit is
// answered: refused. Every test here stubs resolveSession, so the probe never
// actually arrives. If a future test forgets to, it gets "nobody is signed
// in" rather than a session built from the `[]` the data calls receive.
//
// Plain functions, NOT vi.fn().mockResolvedValue(): `restoreMocks` in
// vite.config.ts strips implementations off spies created in a module factory,
// from the very first test. Written as spies these returned `undefined`, the
// dashboard did `.then()` on it, and the resulting render errors were invisible
// because nothing here asserts on dashboard data.
vi.mock('@/lib/axios', () => ({
    default: {
        get: (_url: string, config?: { skipAuthRedirect?: boolean }) => config?.skipAuthRedirect
            ? Promise.reject(Object.assign(new Error('401 (mocked)'), { response: { status: 401 } }))
            : Promise.resolve({ data: [] }),
        post: () => Promise.resolve({ data: {} }),
        interceptors: { request: { use: () => { } }, response: { use: () => { } } },
    },
}));

// The spinner is the only element with this class; App renders it while
// isLoading is true and nothing else at all.
const spinner = (container: HTMLElement) => container.querySelector('.animate-spin');

// A test that starts at `/` and then clicks a nav item must wait for this
// first. The catch-all route redirects `/` to /dashboard from an effect, and
// the shell's nav is already on screen before that effect runs. A click in
// that gap navigates, and the redirect then lands on top of it, so the app
// stays on /dashboard and the clicked page never shows.
const landedOnDashboard = () => waitFor(() => expect(window.location.pathname).toBe('/dashboard'));

// Spy restoration and localStorage clearing are owned centrally --
// `restoreMocks` in vite.config.ts and the afterEach in src/test/setup.ts.
describe('App bootstrap', () => {
    beforeEach(() => {
        window.history.pushState({}, '', '/');
    });

    it('renders the login page when there is no session', async () => {
        vi.spyOn(authService, 'resolveSession').mockResolvedValue(null);

        const { container } = render(<App />);

        await waitFor(() => expect(spinner(container)).toBeNull());
        // Assert the login form is actually there. A "no error screen shown"
        // check would be inert here: ErrorBoundary wraps <App/> in main.tsx and
        // is not in this tree at all, so it can never render. main.test.tsx
        // makes that assertion where it means something.
        //
        // The longer limit is for a starved machine. This is the file's first
        // render of the login page and its first role query, and both cost
        // more the first time. Under heavy CPU load the page was once still
        // not on screen when the default one-second wait ran out.
        expect(await screen.findByRole('button', { name: /sign in/i }, { timeout: 3000 })).toBeInTheDocument();
    });

    it('fires no XHR or fetch from the public login page', async () => {
        // SEC-M13 / FE-H1-1. A connection widget used to sit on this page and,
        // on mount, fire a bare request at a hardcoded backend address, then
        // display that address to anyone who could reach the login screen
        // whenever the request failed, and log the raw error. Deleting it left no test able to notice it coming
        // back -- without a mock it just fires the request and logs an error,
        // so this watches the transports themselves.
        //
        // One blind spot: a request made through `@/lib/axios` never reaches a
        // transport here, because this file mocks that client wholesale.
        vi.spyOn(authService, 'resolveSession').mockResolvedValue(null);
        const open = vi.spyOn(XMLHttpRequest.prototype, 'open');
        const fetchSpy = vi.spyOn(globalThis, 'fetch');

        render(<App />);
        expect(await screen.findByRole('button', { name: /sign in/i })).toBeInTheDocument();
        // A bare axios or fetch call reaches the transport synchronously inside
        // the effect, so findByRole's await already catches it (the restored
        // widget is caught without this line). A request issued after an
        // `await` inside the effect lands a few microtasks later; one more
        // macrotask covers that case too.
        await new Promise(resolve => setTimeout(resolve, 0));

        expect(open).not.toHaveBeenCalled();
        expect(fetchSpy).not.toHaveBeenCalled();
    });

    it('renders the authenticated shell when the cookie is recognised', async () => {
        vi.spyOn(authService, 'resolveSession').mockResolvedValue(SESSION);

        const { container } = render(<App />);

        await waitFor(() => expect(spinner(container)).toBeNull());
        expect(await screen.findByText(/Master Admin/i)).toBeInTheDocument();
    });

    describe('hostile localStorage', () => {
        // Each of these was capable of blanking the application before this
        // ticket. They pass now for a structural reason rather than a defensive
        // one: nothing reads these keys any more.
        const POISON = [
            ['corrupt JSON', '{{{not json'],
            ['empty string', ''],
            ['a bare literal', 'null'],
            ['an array where an object was expected', '[]'],
            ['a number', '42'],
        ] as const;

        it.each(POISON)('survives a cached user that is %s', async (_label, value) => {
            localStorage.setItem('user', value);
            localStorage.setItem('token', 'LEFTOVER_PRE_SEC_H9_TOKEN');
            vi.spyOn(authService, 'resolveSession').mockResolvedValue(null);

            const { container } = render(<App />);

            // The regression: the spinner must clear AND something must render.
            // Before the fix the throw preempted setIsLoading(false), so this
            // hung on a spinner forever. `waitFor` on the content too, because
            // the router's redirect to /login lands a tick after loading ends.
            await waitFor(() => expect(spinner(container)).toBeNull());
            await waitFor(() => expect(container).not.toBeEmptyDOMElement());
        });
    });

    it('clears the spinner, alerts, and shows the login page when the session probe rejects outright', async () => {
        // resolveSession swallows a 401 as null (that path is covered by "renders
        // the login page..." above) and swallows a recognised-but-fault-loading
        // capabilities failure as its own thrown Error (SEC-H10) -- so the only
        // way resolveSession() itself rejects outright is something establishSession
        // did not anticipate: a DNS failure, a timeout. isLoading lives in a
        // `finally` precisely so this cannot hang, and establishSession's catch is
        // unconditional, so it alerts here exactly as it does for the narrower
        // SEC-H10 case.
        const alerted = vi.spyOn(window, 'alert').mockImplementation(() => { });
        vi.spyOn(authService, 'resolveSession').mockRejectedValue(new Error('network down'));

        const { container } = render(<App />);

        await waitFor(() => expect(spinner(container)).toBeNull());
        expect(alerted).toHaveBeenCalled();
        expect(await screen.findByRole('button', { name: /sign in/i })).toBeInTheDocument();
    });

    it('FE-H5: unmounting aborts the session probe, and the cancelled probe raises no alert', async () => {
        // The stub rejects on abort with the signal's reason, as the real
        // resolveSession does. That rejection reaches establishSession's
        // catch, which must recognise it as a cancel and not a fault.
        const alerted = vi.spyOn(window, 'alert').mockImplementation(() => { });
        let probeSignal: AbortSignal | undefined;
        const probe = vi.spyOn(authService, 'resolveSession').mockImplementation(signal => {
            probeSignal = signal;
            return new Promise((_, reject) => {
                signal?.addEventListener('abort', () => reject(signal.reason));
            });
        });

        const { unmount } = render(<App />);
        await waitFor(() => expect(probe).toHaveBeenCalledTimes(1));
        expect(probeSignal?.aborted).toBe(false);

        unmount();
        expect(probeSignal?.aborted).toBe(true);
        await act(async () => { });
        expect(alerted).not.toHaveBeenCalled();
    });

    it('FE-H5: under StrictMode the cancelled first probe neither alerts nor ends the loading state', async () => {
        // main.tsx renders <StrictMode>, so in development the mount effect
        // runs, is cleaned up, and runs again: the first probe is cancelled at
        // once and the second is the real one. The cancelled probe rejects
        // first. If its null were taken as the answer, the login page would
        // flash until the second probe lands.
        const alerted = vi.spyOn(window, 'alert').mockImplementation(() => { });
        const probes: Array<{ signal?: AbortSignal; resolve: (session: Session | null) => void }> = [];
        vi.spyOn(authService, 'resolveSession').mockImplementation(signal =>
            new Promise((resolve, reject) => {
                probes.push({ signal, resolve });
                signal?.addEventListener('abort', () => reject(signal.reason));
            }));

        const { container } = render(<StrictMode><App /></StrictMode>);
        await waitFor(() => expect(probes).toHaveLength(2));
        expect(probes.map(p => p.signal?.aborted)).toEqual([true, false]);
        await act(async () => { });

        expect(spinner(container)).not.toBeNull();
        expect(screen.queryByRole('button', { name: /sign in/i })).toBeNull();
        expect(alerted).not.toHaveBeenCalled();

        probes[1].resolve(SESSION);
        expect(await screen.findByText(/Master Admin/i)).toBeInTheDocument();
    });
});

describe('SEC-H10: the /admin route guard', () => {
    beforeEach(() => {
        window.history.pushState({}, '', '/');
    });

    it('offers the admin nav item and route to a MANAGE_PERSONNEL holder', async () => {
        vi.spyOn(authService, 'resolveSession').mockResolvedValue(SESSION);

        render(<App />);

        expect(await screen.findByText('ניהול מערכת')).toBeInTheDocument();
        await landedOnDashboard();
        fireEvent.click(screen.getByText('ניהול מערכת'));

        expect(await screen.findByText(/שיוך משתמשים לקבוצות/)).toBeInTheDocument();
    });

    it('hides the nav item and refuses the route to an ungranted user, even by URL', async () => {
        // The regression this ticket exists for: before this ticket, typing the
        // path rendered the panel regardless of the (then-nonexistent) nav
        // filter. Navigating directly, not clicking, is the point -- a hidden
        // button was never the actual hole.
        window.history.pushState({}, '', '/admin');
        vi.spyOn(authService, 'resolveSession').mockResolvedValue(SESSION_NO_ADMIN);

        const { container } = render(<App />);

        await waitFor(() => expect(spinner(container)).toBeNull());
        // The panel never mounted -- not hidden, not errored, absent.
        expect(screen.queryByText(/שיוך משתמשים לקבוצות/)).toBeNull();
        expect(screen.queryByText('ניהול מערכת')).toBeNull();
        // Landed in the shell (the `*` catch-all to /dashboard), not blanked.
        expect(await screen.findByText(/Master Admin/i)).toBeInTheDocument();
    });
});

describe('API-H6: the /catalog route guard', () => {
    // Each session holds exactly ONE of the two global verbs. SESSION holds
    // both, so it cannot tell a guard keyed on MANAGE_CATALOG from one keyed
    // on MANAGE_PERSONNEL -- or from one keyed on "holds any system verb".
    // Brigade Tech Commander is the real account shaped like the first one.
    const SESSION_CATALOG_ONLY = {
        ...SESSION,
        capabilities: { system: ['MANAGE_CATALOG'], anywhere: [] },
    };
    const SESSION_PERSONNEL_ONLY = {
        ...SESSION,
        capabilities: { system: ['MANAGE_PERSONNEL'], anywhere: [] },
    };
    const CATALOG_NAV = 'אישור סוגי תקלות';
    // Text only the queue page renders -- its subtitle.
    const QUEUE_PAGE = /אישור מוסיף את הסוג לרשימת הבחירה/;

    beforeEach(() => {
        window.history.pushState({}, '', '/');
    });

    it('offers the queue to a MANAGE_CATALOG holder who is not an admin', async () => {
        vi.spyOn(authService, 'resolveSession').mockResolvedValue(SESSION_CATALOG_ONLY);

        render(<App />);

        expect(await screen.findByText(CATALOG_NAV)).toBeInTheDocument();
        expect(screen.queryByText('ניהול מערכת')).toBeNull();
        await landedOnDashboard();
        fireEvent.click(screen.getByText(CATALOG_NAV));

        expect(await screen.findByText(QUEUE_PAGE)).toBeInTheDocument();
    });

    it('refuses the queue to an admin without MANAGE_CATALOG, even by URL', async () => {
        window.history.pushState({}, '', '/catalog');
        vi.spyOn(authService, 'resolveSession').mockResolvedValue(SESSION_PERSONNEL_ONLY);

        const { container } = render(<App />);

        await waitFor(() => expect(spinner(container)).toBeNull());
        // The admin's own item is still there, so the shell did render ...
        expect(await screen.findByText('ניהול מערכת')).toBeInTheDocument();
        // ... and the queue never mounted, from the nav or from the URL.
        expect(screen.queryByText(CATALOG_NAV)).toBeNull();
        expect(screen.queryByText(QUEUE_PAGE)).toBeNull();
    });

    it('refuses the queue to an ungranted user by URL, landing in the shell', async () => {
        window.history.pushState({}, '', '/catalog');
        vi.spyOn(authService, 'resolveSession').mockResolvedValue(SESSION_NO_ADMIN);

        const { container } = render(<App />);

        await waitFor(() => expect(spinner(container)).toBeNull());
        expect(await screen.findByText(/Master Admin/i)).toBeInTheDocument();
        expect(screen.queryByText(CATALOG_NAV)).toBeNull();
        expect(screen.queryByText(QUEUE_PAGE)).toBeNull();
    });
});

describe('login', () => {
    // Queried as raw inputs rather than by role: `<input type="password">` has
    // no implicit ARIA role, so getAllByRole('textbox') returns only the
    // identifier field and the destructure hands back undefined.
    const submitLogin = async (container: HTMLElement) => {
        const [id, password] = Array.from(container.querySelectorAll('input'));
        fireEvent.change(id, { target: { value: 'u_master' } });
        fireEvent.change(password, { target: { value: 'secret' } });
        fireEvent.click(screen.getByRole('button', { name: /sign in/i }));
    };

    it('enters the shell when the profile resolves', async () => {
        vi.spyOn(authService, 'resolveSession')
            .mockResolvedValueOnce(null)      // cold load: nobody signed in
            .mockResolvedValue(SESSION);      // after credentials are accepted
        vi.spyOn(authService, 'login').mockResolvedValue(undefined);

        const { container } = render(<App />);
        await screen.findByRole('button', { name: /sign in/i });
        await submitLogin(container);

        expect(await screen.findByText(/Master Admin/i)).toBeInTheDocument();
    });

    it('does not report a login failure when the credentials were accepted', async () => {
        // The regression this pair exists for, and it needs the profile probe
        // to actually FAIL -- mocking it to succeed tests the happy path twice.
        //
        // Once /login returns 200 the cookie EXISTS: the login worked. If the
        // profile probe then comes back empty, telling the operator "login
        // failed" sends them to re-enter credentials for a session they are
        // already holding.
        vi.spyOn(authService, 'resolveSession')
            .mockResolvedValueOnce(null)      // cold load: nobody signed in
            .mockResolvedValue(null);         // profile probe comes back empty
        const login = vi.spyOn(authService, 'login').mockResolvedValue(undefined);

        const { container } = render(<App />);
        await screen.findByRole('button', { name: /sign in/i });
        await submitLogin(container);

        await waitFor(() => expect(login).toHaveBeenCalled());
        // LoginPage renders this string ONLY when onLogin rejects
        // (LoginPage.tsx's onSubmit catch). Its absence is the whole assertion.
        expect(screen.queryByText(/שגיאת התחברות/)).toBeNull();
    });
});

describe('logout', () => {
    // AppShell renders a logout button in each of its expanded/collapsed
    // sidebar branches; either will do.
    const clickLogout = async () => {
        const [button] = await screen.findAllByRole('button', { name: /log ?out|התנתק/i });
        button.click();
    };

    it('drops the authenticated shell on success', async () => {
        vi.spyOn(authService, 'resolveSession').mockResolvedValue(SESSION);
        vi.spyOn(authService, 'logout').mockResolvedValue(undefined);

        render(<App />);
        await clickLogout();

        await waitFor(() => expect(screen.queryByText(/Master Admin/i)).toBeNull());
    });

    it('does NOT fake a logout it could not perform', async () => {
        // The shared-terminal case, and the security property that matters.
        // Only the server can clear an httpOnly cookie, so a failed logout may
        // leave the session live. A UI that renders "logged out" over a live
        // session hands the next person at that terminal the previous
        // operator's account on the first refresh.
        //
        // Asserting the property (state is not cleared) rather than the
        // recovery mechanism (a location.assign to /login, which jsdom will not
        // perform anyway) -- the mechanism may change, the property must not.
        vi.spyOn(console, 'error').mockImplementation(() => { });
        // jsdom has no real alert; without a stub it logs "not implemented".
        const alerted = vi.spyOn(window, 'alert').mockImplementation(() => { });
        vi.spyOn(authService, 'resolveSession').mockResolvedValue(SESSION);
        vi.spyOn(authService, 'logout').mockRejectedValue(new Error('network down'));

        render(<App />);
        await clickLogout();

        // The operator is TOLD. Silence here reads as a successful logout,
        // which on a shared terminal is the dangerous misreading.
        await waitFor(() => expect(alerted).toHaveBeenCalled());
        // And local state was not cleared -- the session that still exists is
        // still shown, rather than a logged-out UI painted over a live cookie.
        expect(screen.queryByText(/Master Admin/i)).not.toBeNull();
    });
});
