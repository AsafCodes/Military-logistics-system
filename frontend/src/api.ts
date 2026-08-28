import axios from 'axios';

// This is the SECOND axios instance in the tree; lib/axios.ts is the other, and
// the two carry different 401 policies (this one reloads, that one navigates to
// /login and honours skipAuthRedirect). Collapsing them is FE-H1 and is
// deliberately out of SEC-H9's scope -- said here rather than only in
// lib/axios.test.ts, so whoever edits one client learns the other exists.

// 1. Configure axios instance
const api = axios.create({
    baseURL: import.meta.env.VITE_API_URL || 'http://localhost:8000',
    // SEC-H9: send the httpOnly session cookie on cross-origin requests. The
    // frontend is served from :3000 and the API from :8000, so without this the
    // browser withholds the cookie and every call 401s.
    withCredentials: true,
});

// 2. Request Interceptor: deliberately absent.
// This used to read localStorage['token'] and set an Authorization header.
// There is no token in web storage any more -- the browser attaches the cookie
// itself, which is the entire point of SEC-H9.

// 3. Response Interceptor: Handle 401
api.interceptors.response.use(
    (response) => response,
    (error) => {
        if (error.response && error.response.status === 401) {
            console.warn('Unauthorized - redirecting to login');
            // Nothing to clear here: the server has already refused the cookie,
            // and this code cannot read or delete an httpOnly one regardless.
            window.location.reload();
        }
        return Promise.reject(error);
    }
);

export default api;
