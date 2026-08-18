import axios from 'axios';

// 1. Configure axios instance
const api = axios.create({
    baseURL: import.meta.env.VITE_API_URL || 'http://localhost:8000',
});

// 2. Request Interceptor: Add Token
api.interceptors.request.use(
    (config) => {
        const token = localStorage.getItem('token');
        if (token) {
            config.headers.Authorization = `Bearer ${token}`;
        }
        return config;
    },
    (error) => Promise.reject(error)
);

// 3. Response Interceptor: Handle 401
api.interceptors.response.use(
    (response) => response,
    (error) => {
        if (error.response && error.response.status === 401) {
            console.warn('Unauthorized - redirecting to login');
            localStorage.removeItem('token');
            // Optional: Force reload or dispatch an event if using a global store
            // For now, removing the token will cause App.tsx to switch view on next render/check
            // or we can force a reload:
            window.location.reload();
        }
        return Promise.reject(error);
    }
);

export default api;
