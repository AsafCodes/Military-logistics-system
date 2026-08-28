/**
 * Axios Instance with Interceptors
 * Centralized HTTP client configuration
 */
import axios from 'axios';

declare module 'axios' {
    export interface AxiosRequestConfig {
        /**
         * Suppress the 401 -> /login redirect for this request.
         *
         * For the one caller that EXPECTS to be refused: the cold-load session
         * probe. Without it, every anonymous visit 401s and triggers a full
         * page navigation to a page the visitor is already on.
         *
         * Honoured by THIS client only. `declare module` widens
         * AxiosRequestConfig globally, so api.ts advertises the option in its
         * types too and ignores it -- that client reloads on any 401. The two
         * clients having different 401 policies at all is FE-H1.
         */
        skipAuthRedirect?: boolean;
    }
}

// Base URL from environment or fallback
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

// Create axios instance
const apiClient = axios.create({
    baseURL: API_BASE_URL,
    // No default Content-Type. Axios sets one for requests that actually carry
    // a JSON body; setting it here put `application/json` on GETs too, and that
    // is not a CORS-safelisted value -- so every cross-origin GET paid an
    // OPTIONS preflight before it could start. That cost doubled the cold-load
    // session probe, which SEC-H9 moved onto the critical path: nothing renders
    // until /users/me answers, so it was two round trips to first paint instead
    // of one. Pinned by axios.test.ts.
    timeout: 10000, // 10 second timeout
    // SEC-H9: the session is an httpOnly cookie now. This is what makes the
    // browser attach it to cross-origin requests (the frontend is served from
    // :3000 and the API from :8000), and main.py's CORS already answers with
    // allow_credentials plus an explicit origin list.
    withCredentials: true,
});

// ============ REQUEST INTERCEPTOR ============
// Deliberately absent. There is no longer a token in web storage to read and
// attach -- the browser sends the cookie itself. A request interceptor here is
// how the token would find its way back into localStorage.

// ============ RESPONSE INTERCEPTOR ============
// Global error handling - redirect to login on 401
apiClient.interceptors.response.use(
    (response) => {
        return response;
    },
    (error) => {
        // Nothing to clear on a 401: the session lives in a cookie the server
        // has already refused, and this code could not read or delete it anyway.
        if (axios.isAxiosError(error) && error.response?.status === 401) {
            if (error.config?.skipAuthRedirect) {
                return Promise.reject(error);
            }

            // Only redirect if not already on login page
            if (!window.location.pathname.includes('/login')) {
                window.location.href = '/login';
            }
        }
        return Promise.reject(error);
    }
);

export default apiClient;
