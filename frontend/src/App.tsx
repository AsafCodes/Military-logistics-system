import { useState, useEffect } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ConnectionTest from './components/ConnectionTest';
import Login from './components/Login';
import Dashboard from './components/Dashboard';

const queryClient = new QueryClient();

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(false);
  const [isLoading, setIsLoading] = useState<boolean>(true);

  useEffect(() => {
    // Check for token on mount
    const token = localStorage.getItem('token');
    setIsAuthenticated(!!token);
    setIsLoading(false);
  }, []);

  const handleLogin = () => {
    setIsAuthenticated(true);
  };

  const handleLogout = () => {
    localStorage.removeItem('token');
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
            <Login onLogin={handleLogin} />

            {/* Optional: Keep connection test visible for debugging during development, or hide it */}
            <div className="fixed bottom-4 right-4 opacity-50 hover:opacity-100 transition-opacity">
              <ConnectionTest />
            </div>
          </>
        ) : (
          /* Show Dashboard if authenticated */
          <Dashboard onLogout={handleLogout} />
        )}
      </div>
    </QueryClientProvider>
  );
}

export default App;
