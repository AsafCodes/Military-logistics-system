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
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import App from './App';
import { authService } from './services';
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

// ConnectionTest fires a real XHR at a hardcoded 127.0.0.1:8000 on mount and
// dumps the resulting network error to stderr. Stubbed so a passing run reads
// as one. Deleting the component for real is SEC-M13, a separate entry.
vi.mock('./components/shared/ConnectionTest', () => ({
    default: () => null,
}));

// The dashboard loads its own data on mount through the other axios client.
// These tests are about the session bootstrap, not that data, and letting the
// requests fly produces real network errors in the output.
//
// Plain functions, NOT vi.fn().mockResolvedValue(): `restoreMocks` in
// vite.config.ts strips implementations off spies created in a module factory,
// from the very first test. Written as spies these returned `undefined`, the
// dashboard did `.then()` on it, and the resulting render errors were invisible
// because nothing here asserts on dashboard data.
vi.mock('@/api', () => ({
    default: {
        get: () => Promise.resolve({ data: [] }),
        post: () => Promise.resolve({ data: {} }),
        interceptors: { request: { use: () => { } }, response: { use: () => { } } },
    },
}));

// The spinner is the only element with this class; App renders it while
// isLoading is true and nothing else at all.
const spinner = (container: HTMLElement) => container.querySelector('.animate-spin');

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
        expect(await screen.findByRole('button', { name: /sign in/i })).toBeInTheDocument();
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
});

describe('SEC-H10: the /admin route guard', () => {
    beforeEach(() => {
        window.history.pushState({}, '', '/');
    });

    it('offers the admin nav item and route to a MANAGE_PERSONNEL holder', async () => {
        vi.spyOn(authService, 'resolveSession').mockResolvedValue(SESSION);

        render(<App />);

        expect(await screen.findByText('ניהול מערכת')).toBeInTheDocument();
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
