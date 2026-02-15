/**
 * Authentication Service
 * Handles login, logout, and user retrieval
 */
import apiClient from '@/lib/axios';
import type { User, TokenResponse } from '@/types';

export interface LoginCredentials {
    personalNumber: string;
    password: string;
}

class AuthService {
    /**
     * Login with credentials
     * Uses form-urlencoded format as required by OAuth2PasswordRequestForm
     */
    async login(credentials: LoginCredentials): Promise<TokenResponse> {
        const formData = new URLSearchParams();
        formData.append('username', credentials.personalNumber);
        formData.append('password', credentials.password);

        const response = await apiClient.post<TokenResponse>('/login', formData, {
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
        });

        // Save token to localStorage
        if (response.data.access_token) {
            localStorage.setItem('token', response.data.access_token);
        }

        return response.data;
    }

    /**
     * Logout - clear stored credentials
     */
    logout(): void {
        localStorage.removeItem('token');
        localStorage.removeItem('user');
    }

    /**
     * Get current logged-in user
     */
    async getMe(): Promise<User> {
        const response = await apiClient.get<User>('/users/me');

        // Cache user info
        localStorage.setItem('user', JSON.stringify(response.data));

        return response.data;
    }

    /**
     * Get cached user from localStorage
     */
    getCachedUser(): User | null {
        const userStr = localStorage.getItem('user');
        return userStr ? JSON.parse(userStr) : null;
    }

    /**
     * Check if user is authenticated
     */
    isAuthenticated(): boolean {
        return !!localStorage.getItem('token');
    }

    /**
     * Get stored token
     */
    getToken(): string | null {
        return localStorage.getItem('token');
    }
}

// Export singleton instance
export const authService = new AuthService();
export default authService;
