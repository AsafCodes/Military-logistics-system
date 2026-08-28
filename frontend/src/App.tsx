import { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ConnectionTest from './components/shared/ConnectionTest';
import { LoginPage, type LoginFormValues } from './features/auth';
import AppShell from './components/layout/AppShell';
import { authService } from './services';
import type { User } from '@/types';

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
  user,
  onLogout,
}: {
  // Not nullable: this layout renders only where `user !== null`, and typing it
  // otherwise invites callers to render an authenticated shell for nobody.
  user: User;
  onLogout: () => void;
}) {
  return (
    <AppShell user={user} onLogout={onLogout}>
      <Routes>
        <Route path="/dashboard" element={<DashboardPage onLogout={onLogout} />} />
        <Route path="/equipment" element={<EquipmentPage />} />
        <Route path="/maintenance" element={<MaintenancePage />} />
        <Route path="/reports" element={<GeneralReportPage />} />
        <Route path="/admin" element={<AdminPanel onClose={() => { }} />} />
        {/* Default redirect */}
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </AppShell>
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
  const [user, setUser] = useState<User | null>(null);

  // SEC-H9. This effect used to read a cached user out of localStorage and
  // JSON.parse it unguarded, THEN call setIsLoading(false) on the next line --
  // so a single malformed character in that value threw before the spinner
  // could clear, and the application blanked permanently with no error boundary
  // to catch it. There is no cache and no synchronous parse left: the server is
  // asked who we are, and isLoading clears in `finally` on every outcome,
  // including a rejection.
  useEffect(() => {
    let cancelled = false;

    authService.resolveSession()
      .then(me => {
        if (!cancelled) setUser(me);
      })
      .catch(() => {
        // resolveSession already answers a refused session with null, so
        // reaching here means something worse -- and an uncaught rejection in a
        // mount effect is how this bug presented the first time. Treated as
        // "not signed in": the shell must not render for a session we failed to
        // establish.
        if (!cancelled) setUser(null);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => { cancelled = true; };
  }, []);

  const handleLogin = async (values: LoginFormValues) => {
    await authService.login(values);

    // Past this line the cookie EXISTS -- the credentials were accepted. So a
    // failure fetching the profile must not surface as "login failed": the
    // login worked, and telling the operator otherwise sends them to re-enter
    // credentials for a session they already hold.
    //
    // resolveSession rather than a throwing fetch, and the SAME call the cold
    // path uses: one request, null instead of an exception, and one answer to
    // "who is signed in" rather than two spellings of it. A null here leaves
    // the login form up with the cookie already set, so a second attempt
    // succeeds against the session they now hold.
    setUser(await authService.resolveSession());
  };

  const handleLogout = async () => {
    // Only the server can end the session now -- the cookie is httpOnly, so
    // this code can neither read nor delete it.
    try {
      await authService.logout();
      setUser(null);
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
          {user === null ? (
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
            <Route path="/*" element={<AuthenticatedLayout user={user} onLogout={handleLogout} />} />
          )}
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

export default App;
