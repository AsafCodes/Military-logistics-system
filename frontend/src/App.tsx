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
  user: User | null;
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
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(false);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    const loggedIn = authService.isAuthenticated();
    setIsAuthenticated(loggedIn);
    if (loggedIn) {
      // Load cached user immediately for sidebar rendering
      const cached = authService.getCachedUser();
      if (cached) setUser(cached);
      // Refresh user profile in background
      authService.getMe().then(u => setUser(u)).catch(() => { });
    }
    setIsLoading(false);
  }, []);

  const handleLogin = async (values: LoginFormValues) => {
    await authService.login(values);
    const me = await authService.getMe();
    setUser(me);
    setIsAuthenticated(true);
  };

  const handleLogout = () => {
    authService.logout();
    setIsAuthenticated(false);
    setUser(null);
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
          {!isAuthenticated ? (
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
