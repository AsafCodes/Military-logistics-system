import { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ConnectionTest from './components/shared/ConnectionTest';
import { LoginPage, type LoginFormValues } from './features/auth';
import AppShell from './components/layout/AppShell';
import { authService } from './services';
import { CAPABILITY, CapabilitiesContext, hasSystem } from '@/lib/capabilities';
import type { Session } from '@/types';

// Pages
import { DashboardPage } from './features/dashboard';
import AdminPanel from './features/dashboard/components/AdminPanel';
import GeneralReportPage from './features/reports/components/GeneralReportPage';
import EquipmentPage from './features/equipment/components/EquipmentPage';
import MaintenancePage from './features/maintenance/components/MaintenancePage';

const queryClient = new QueryClient();

// ============================================================
// Authenticated Layout — wraps all protected routes in AppShell
// ============================================================

function AuthenticatedLayout({
  session,
  onLogout,
}: {
  session: Session;
  onLogout: () => void;
}) {
  // SEC-H10. /admin's real gate is MANAGE_PERSONNEL, a GLOBAL capability --
  // authz.may_global answers it as an exact yes/no, so hasSystem here is not
  // an approximation the way hasAnywhere would be for a positional verb.
  //
  // Non-registration, not a redirect drawn after the fact: when the caller
  // lacks the capability, the Route element below simply never exists, and
  // the `*` catch-all two lines down absorbs /admin exactly like any other
  // unknown path. Typing the URL cannot render a panel that was never
  // registered -- this is what makes the guard structural rather than
  // cosmetic.
  const isAdmin = hasSystem(session.capabilities, CAPABILITY.MANAGE_PERSONNEL);

  return (
    <CapabilitiesContext.Provider value={session.capabilities}>
      <AppShell user={session.user} onLogout={onLogout}>
        <Routes>
          <Route path="/dashboard" element={<DashboardPage onLogout={onLogout} />} />
          <Route path="/equipment" element={<EquipmentPage />} />
          <Route path="/maintenance" element={<MaintenancePage />} />
          <Route path="/reports" element={<GeneralReportPage />} />
          {isAdmin && (
            <Route path="/admin" element={<AdminPanel onClose={() => { }} />} />
          )}
          {/* Default redirect. Also where /admin lands for anyone the Route
              above wasn't registered for -- same as any other unknown path. */}
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </AppShell>
    </CapabilitiesContext.Provider>
  );
}

// ============================================================
// App Root
// ============================================================

function App() {
  const [isLoading, setIsLoading] = useState<boolean>(true);

  // The whole of the session state. There is deliberately no separate
  // `isAuthenticated` boolean beside it: authenticated MEANS the server told us
  // who we are, so a second field would be a copy of this one that some future
  // edit gets to desynchronise -- in the two directions that matter, an
  // authenticated shell rendered with no user, or a login page rendered over a
  // live session.
  const [session, setSession] = useState<Session | null>(null);

  // SEC-H10. resolveSession() now throws when the cookie IS recognised but
  // permissions could not be loaded (a 5xx on the capabilities call) -- a
  // server fault, not a signed-out visitor, and reporting it as "signed out"
  // would be a lie. Both the mount effect and handleLogin route through this
  // one helper so that fault surfaces identically on both paths: told, not
  // silently mis-rendered as a stripped or absent session.
  //
  // Resolves rather than setting state itself -- the mount effect below still
  // needs its own `cancelled` guard around the resulting setState, which a
  // shared helper cannot own on the caller's behalf. alert() matches the
  // convention handleLogout already uses below, and the eight pre-existing
  // calls across the feature pages.
  const establishSession = async (): Promise<Session | null> => {
    try {
      return await authService.resolveSession();
    } catch {
      window.alert('טעינת ההרשאות נכשלה. נסה לרענן את הדף.');
      return null;
    }
  };

  // SEC-H9. This effect used to read a cached user out of localStorage and
  // JSON.parse it unguarded, THEN call setIsLoading(false) on the next line --
  // so a single malformed character in that value threw before the spinner
  // could clear, and the application blanked permanently with no error boundary
  // to catch it. There is no cache and no synchronous parse left: the server is
  // asked who we are, and isLoading clears in `finally` on every outcome,
  // including a rejection.
  useEffect(() => {
    let cancelled = false;

    (async () => {
      // establishSession's own try/catch means it never rejects, so no
      // try/finally is needed here to guarantee this runs -- only the
      // `cancelled` guard is load-bearing, against a setState after unmount.
      const result = await establishSession();
      if (!cancelled) setSession(result);
      if (!cancelled) setIsLoading(false);
    })();

    return () => { cancelled = true; };
  }, []);

  const handleLogin = async (values: LoginFormValues) => {
    await authService.login(values);

    // Past this line the cookie EXISTS -- the credentials were accepted. So a
    // failure establishing the session must not surface as "login failed":
    // the login worked, and telling the operator otherwise sends them to
    // re-enter credentials for a session they already hold. establishSession
    // alerts (rather than throwing) on that failure, so the login form stays
    // up with the cookie already set and a second attempt succeeds.
    setSession(await establishSession());
  };

  const handleLogout = async () => {
    // Only the server can end the session now -- the cookie is httpOnly, so
    // this code can neither read nor delete it.
    try {
      await authService.logout();
      setSession(null);
    } catch (error) {
      // Caught, not rethrown: nothing awaits this handler, so a rejection would
      // escape as an unhandled promise rejection.
      console.error('Logout request failed:', error);

      // Crucially, we do NOT clear local state here. The cookie is httpOnly, so
      // this code can neither read nor delete it; if the request failed, the
      // session may well still be live. Showing a logged-out UI over a live
      // session is the dangerous direction -- on a shared terminal the next
      // person presses F5, resolveSession succeeds, and they are handed the
      // previous operator's account.
      //
      // Tell the operator before re-deriving. Without this the click reads as
      // a no-op: if the session survived, resolveSession succeeds on the next
      // load and the catch-all lands them straight back on /dashboard, which
      // looks like nothing happened rather than like a logout that failed.
      // alert() is what this codebase already uses to report a failed
      // operation (EquipmentPage, MaintenancePage, AdminPanel all do).
      window.alert('ההתנתקות נכשלה — ייתכן שהחיבור עדיין פעיל. סגור את הדפדפן.');

      // Then re-derive from the server, which is this ticket's whole principle.
      // The operator lands wherever the truth actually is: back in the app if
      // the session survived, on the login page if it did not.
      window.location.assign('/login');
    }
  };

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="animate-spin rounded-full h-10 w-10 border-2 border-primary border-t-transparent" />
      </div>
    );
  }

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          {session === null ? (
            <>
              <Route
                path="/login"
                element={
                  <>
                    <LoginPage onLogin={handleLogin} />
                    <div className="fixed bottom-4 right-4 opacity-50 hover:opacity-100 transition-opacity">
                      <ConnectionTest />
                    </div>
                  </>
                }
              />
              <Route path="*" element={<Navigate to="/login" replace />} />
            </>
          ) : (
            <Route path="/*" element={<AuthenticatedLayout session={session} onLogout={handleLogout} />} />
          )}
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

export default App;
