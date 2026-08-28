/**
 * Authentication Service
 * Handles login, logout, and user retrieval
 *
 * SEC-H9: this service stores NOTHING. The session lives in an httpOnly cookie
 * the browser attaches on its own and this code cannot read, so there is no
 * token to hand out, no cached user to trust, and no `isAuthenticated()` to
 * answer from local state. Identity is whatever `/users/me` says it is.
 */
import apiClient from '@/lib/axios';
import type { User } from '@/types';

export interface LoginCredentials {
    personalNumber: string;
    password: string;
}

class AuthService {
    /**
     * Login with credentials
     * Uses form-urlencoded format as required by OAuth2PasswordRequestForm
     *
     * The response body still carries a token (non-browser clients and Swagger
     * authenticate with it). We deliberately ignore it: reading it here is how
     * it ends up persisted again.
     */
    async login(credentials: LoginCredentials): Promise<void> {
        const formData = new URLSearchParams();
        formData.append('username', credentials.personalNumber);
        formData.append('password', credentials.password);

        await apiClient.post('/login', formData, {
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
        });
    }

    /**
     * Logout - ask the server to clear the session cookie.
     *
     * This has to be a round trip now. The cookie is httpOnly, so unlike the
     * old localStorage.removeItem the client physically cannot end its own
     * session; only the server can send the expiring Set-Cookie.
     */
    async logout(): Promise<void> {
        await apiClient.post('/logout');
    }

    /**
     * Ask the server who is signed in. The only answer to that question.
     *
     * Used by both paths -- the cold page load and the moment after a
     * successful login. There was briefly a second, throwing `getMe()` beside
     * this; two spellings of one request meant every caller had to decide which
     * failure mode it wanted, and post-login the throwing one reported "login
     * failed" for a login that had in fact succeeded.
     *
     * Returns null for "not signed in" rather than throwing, because on a first
     * visit that is the ordinary answer and not an error. Opts out of the 401
     * redirect: this probe EXPECTS to be refused when nobody is signed in, and
     * letting the interceptor act on that turns every anonymous visit into a
     * full page navigation.
     */
    async resolveSession(): Promise<User | null> {
        try {
            const response = await apiClient.get<User>('/users/me', {
                skipAuthRedirect: true,
            });
            return response.data;
        } catch {
            return null;
        }
    }
}

// Export singleton instance
export const authService = new AuthService();
export default authService;
