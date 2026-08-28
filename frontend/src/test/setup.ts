import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

// jsdom implements no CSS media query engine, so window.matchMedia is simply
// absent. ThemeToggle calls it in a mount effect, which means ANY test that
// renders the app shell dies on it for a reason unrelated to what it asserts.
// Reports "light" -- a deterministic default beats a random one.
Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
    }),
});

/**
 * The session user these tests render with.
 *
 * Shared so the two files that need it cannot drift into asserting on
 * different shapes of the same thing. Matches schemas.UserResponse minus
 * `last_seen`, which the frontend User type does not declare (API-M3).
 */
export const TEST_USER = {
    id: 1,
    personal_number: 'u_master',
    full_name: 'Master Admin',
    is_active_duty: true,
};

afterEach(() => {
    cleanup();
    // Several tests here deliberately plant hostile values in localStorage --
    // a leftover pre-SEC-H9 token, a corrupt cached user. Clearing between
    // tests keeps one test's poison out of the next one's assertions, and
    // keeps `localStorage.length === 0` meaningful as a regression guard.
    localStorage.clear();
});
