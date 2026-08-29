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
import type { Session } from '@/types';

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
     * Ask the server who is signed in and what they may do. The only answer
     * to either question.
     *
     * Used by both paths -- the cold page load and the moment after a
     * successful login. There was briefly a second, throwing `getMe()` beside
     * this; two spellings of one request meant every caller had to decide which
     * failure mode it wanted, and post-login the throwing one reported "login
     * failed" for a login that had in fact succeeded.
     *
     * SEC-H10 bundles a second request into this one call rather than adding a
     * parallel resolveCapabilities(): the two must arrive together, or a render
     * window opens where the user is known but their authority is not, and a
     * route guard drawn during that window has to decide "loading" versus
     * "denied" with no principled way to tell them apart.
     *
     * Both requests run under allSettled, not all/Promise.all -- the two
     * failure cases mean opposite things and only allSettled can tell them
     * apart by WHICH call failed:
     *
     *   /users/me rejects                -> nobody is signed in. The ordinary
     *                                        first-visit answer, not an error.
     *   /users/me ok, capabilities fails -> the cookie IS recognised. This is
     *                                        a server fault, and reporting it
     *                                        as "signed out" would be a lie.
     *
     * The second case throws (a plain Error -- this codebase has no custom
     * exception types, see backend/authz.py's own note on the same choice) so
     * the caller can tell an operator their permissions could not be
     * established, rather than silently signing them out.
     *
     * Both requests opt out of the 401 redirect: they EXPECT to be refused
     * when nobody is signed in, and letting the interceptor act on that turns
     * every anonymous visit into a full page navigation.
     */
    async resolveSession(): Promise<Session | null> {
        const [userResult, capsResult] = await Promise.allSettled([
            apiClient.get<Session['user']>('/users/me', { skipAuthRedirect: true }),
            apiClient.get<Session['capabilities']>('/users/me/capabilities', {
                skipAuthRedirect: true,
            }),
        ]);

        if (userResult.status === 'rejected') {
            return null;
        }
        if (capsResult.status === 'rejected') {
            throw new Error('Could not load permissions for a recognised session.');
        }

        return { user: userResult.value.data, capabilities: capsResult.value.data };
    }
}

// Export singleton instance
export const authService = new AuthService();
export default authService;
