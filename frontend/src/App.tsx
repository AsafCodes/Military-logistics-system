import { useState, useEffect } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ConnectionTest from './components/shared/ConnectionTest';
import { LoginPage, type LoginFormValues } from './features/auth';
import { DashboardPage } from './features/dashboard';
import { authService } from './services';

const queryClient = new QueryClient();

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(false);
  const [isLoading, setIsLoading] = useState<boolean>(true);

  useEffect(() => {
    // Check for token on mount using auth service
    setIsAuthenticated(authService.isAuthenticated());
    setIsLoading(false);
  }, []);

  const handleLogin = async (values: LoginFormValues) => {
    // Use auth service for login
    await authService.login(values);

    // Fetch user info after login
    await authService.getMe();

    // Update state
    setIsAuthenticated(true);
  };

  const handleLogout = () => {
    // Use auth service for logout
    authService.logout();
    setIsAuthenticated(false);
  };

  if (isLoading) {
    return <div className="min-h-screen flex items-center justify-center bg-gray-100">Loading...</div>;
  }

  return (
    <QueryClientProvider client={queryClient}>
      <div className="min-h-screen bg-gray-100 font-sans text-gray-900">
        {!isAuthenticated ? (
          <>
            {/* Show Login Screen if not authenticated */}
            <LoginPage onLogin={handleLogin} />

            {/* Optional: Keep connection test visible for debugging during development */}
            <div className="fixed bottom-4 right-4 opacity-50 hover:opacity-100 transition-opacity">
              <ConnectionTest />
            </div>
          </>
        ) : (
          /* Show Dashboard if authenticated */
          <DashboardPage onLogout={handleLogout} />
        )}
      </div>
    </QueryClientProvider>
  );
}

export default App;
