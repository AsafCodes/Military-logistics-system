import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import LoginPage from './LoginPage';
import { writeLocal } from '@/lib/safeStorage';

// The globe is drawn in different colours per theme, and the page tells it
// which. The page used to start from "light" and correct itself in an effect,
// so a dark-theme user's globe was drawn light first. These tests record
// every value the globe is given, not only the last one.

const given = vi.hoisted(() => [] as boolean[]);

vi.mock('@/components/ui/NetworkGlobe', () => ({
    default: ({ isDark }: { isDark?: boolean }) => {
        given.push(isDark === true);
        return null;
    },
}));

const html = document.documentElement;
const renderPage = (strict = false) =>
    render(<LoginPage onLogin={() => Promise.resolve()} />, { reactStrictMode: strict });

beforeEach(() => {
    given.length = 0;
});

afterEach(() => {
    html.classList.remove('dark');
});

describe('LoginPage tells the globe the theme', () => {
    it('says dark from the first render when the page is already dark', () => {
        // index.html's script has set the class before React starts.
        html.classList.add('dark');
        writeLocal('theme', 'dark');
        renderPage();

        expect(given.length).toBeGreaterThan(0);
        expect(given).not.toContain(false);
    });

    it('says light throughout on a light page', () => {
        renderPage();

        expect(given.length).toBeGreaterThan(0);
        expect(given).not.toContain(true);
    });

    it('catches up when the class is set while the page is mounting', () => {
        // No class yet, as when index.html's script did not run. The toggle's
        // own effect then sets it, before this page could start listening.
        writeLocal('theme', 'dark');
        renderPage();

        expect(html.classList.contains('dark')).toBe(true);
        expect(given.at(-1)).toBe(true);
    });

    it('follows the toggle in both directions', async () => {
        renderPage();

        fireEvent.click(screen.getByTitle('Switch to Dark Mode'));
        await vi.waitFor(() => expect(given.at(-1)).toBe(true));

        fireEvent.click(screen.getByTitle('Switch to Light Mode'));
        await vi.waitFor(() => expect(given.at(-1)).toBe(false));
    });

    it('ignores class changes that leave the theme as it was', async () => {
        html.classList.add('dark');
        writeLocal('theme', 'dark');
        renderPage();
        const before = given.length;

        html.classList.add('something-else');
        await new Promise(resolve => setTimeout(resolve, 20));
        html.classList.remove('something-else');

        expect(given.length).toBe(before);
    });

    it('stops listening when it unmounts', () => {
        const disconnect = vi.spyOn(MutationObserver.prototype, 'disconnect');
        const { unmount } = renderPage();
        expect(disconnect).not.toHaveBeenCalled();

        unmount();
        expect(disconnect).toHaveBeenCalledTimes(1);
    });

    it('behaves the same under StrictMode', async () => {
        html.classList.add('dark');
        writeLocal('theme', 'dark');
        renderPage(true);
        expect(given).not.toContain(false);

        fireEvent.click(screen.getByTitle('Switch to Light Mode'));
        await vi.waitFor(() => expect(given.at(-1)).toBe(false));
    });
});
