import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { renderToStaticMarkup } from 'react-dom/server';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { ThemeToggle } from './ThemeToggle';

// FE-H6: the theme is read in the state initialiser, not written by a mount
// effect. These tests pin what that read decides, and that the document
// class and the stored choice still follow the button.

const html = document.documentElement;

function systemPrefersDark(prefers: boolean) {
    vi.spyOn(window, 'matchMedia').mockImplementation(
        (query: string) => ({ matches: prefers, media: query }) as MediaQueryList,
    );
}

/** The accessor itself throws, as in a browser that blocks site data. */
function blockStorage() {
    vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => {
        throw new DOMException('The operation is insecure.', 'SecurityError');
    });
}

afterEach(() => {
    vi.restoreAllMocks();
    html.classList.remove('dark');
});

describe('ThemeToggle', () => {
    it('draws the button on the first frame, before any effect has run', () => {
        // renderToStaticMarkup runs no effects, so this is the first frame.
        expect(renderToStaticMarkup(<ThemeToggle />)).toContain('Switch to Dark Mode');
    });

    it('draws the stored theme on the first frame', () => {
        localStorage.setItem('theme', 'dark');
        expect(renderToStaticMarkup(<ThemeToggle />)).toContain('Switch to Light Mode');
    });

    it('starts light with nothing stored and no system preference', () => {
        html.classList.add('dark');
        render(<ThemeToggle />);
        expect(screen.getByTitle('Switch to Dark Mode')).toBeInTheDocument();
        expect(html.classList.contains('dark')).toBe(false);
    });

    it('starts from the stored theme', () => {
        localStorage.setItem('theme', 'dark');
        render(<ThemeToggle />);
        expect(screen.getByTitle('Switch to Light Mode')).toBeInTheDocument();
        expect(html.classList.contains('dark')).toBe(true);
    });

    it('follows the system preference when nothing is stored', () => {
        systemPrefersDark(true);
        render(<ThemeToggle />);
        expect(screen.getByTitle('Switch to Light Mode')).toBeInTheDocument();
        expect(html.classList.contains('dark')).toBe(true);
    });

    it('lets a stored light theme beat a dark system preference', () => {
        localStorage.setItem('theme', 'light');
        systemPrefersDark(true);
        render(<ThemeToggle />);
        expect(screen.getByTitle('Switch to Dark Mode')).toBeInTheDocument();
        expect(html.classList.contains('dark')).toBe(false);
    });

    // Only 'dark' and 'light' are themes. Anything else in storage used to
    // be taken as the theme: the page went light whatever the system said,
    // and the button offered "Light" on a page that already was (or, for an
    // empty string, was not drawn).
    it.each([
        { stored: 'banana', prefersDark: true },
        { stored: 'banana', prefersDark: false },
        { stored: '', prefersDark: true },
        { stored: 'DARK', prefersDark: false },
    ])('treats a stored $stored as nothing stored (system dark: $prefersDark)', ({ stored, prefersDark }) => {
        localStorage.setItem('theme', stored);
        systemPrefersDark(prefersDark);
        render(<ThemeToggle />);

        expect(html.classList.contains('dark')).toBe(prefersDark);
        expect(screen.getByTitle(`Switch to ${prefersDark ? 'Light' : 'Dark'} Mode`)).toBeInTheDocument();

        // And the first click stores a real theme, the other one.
        fireEvent.click(screen.getByRole('button'));
        expect(localStorage.getItem('theme')).toBe(prefersDark ? 'light' : 'dark');
        expect(html.classList.contains('dark')).toBe(!prefersDark);
    });

    it('switches the class, the label and the stored theme on each click', () => {
        render(<ThemeToggle />);

        fireEvent.click(screen.getByRole('button'));
        expect(screen.getByTitle('Switch to Light Mode')).toBeInTheDocument();
        expect(html.classList.contains('dark')).toBe(true);
        expect(localStorage.getItem('theme')).toBe('dark');

        fireEvent.click(screen.getByRole('button'));
        expect(screen.getByTitle('Switch to Dark Mode')).toBeInTheDocument();
        expect(html.classList.contains('dark')).toBe(false);
        expect(localStorage.getItem('theme')).toBe('light');
    });

    it('stores nothing until the button is clicked', () => {
        systemPrefersDark(true);
        render(<ThemeToggle />);
        expect(localStorage.getItem('theme')).toBeNull();
    });

    it('renders and still switches when storage is blocked', () => {
        systemPrefersDark(true);
        blockStorage();
        // A throw inside the click handler does not fail the click: React
        // reports it as an `error` event on the window.
        const uncaught = vi.fn((event: ErrorEvent) => event.preventDefault());
        window.addEventListener('error', uncaught);

        try {
            render(<ThemeToggle />);
            expect(screen.getByTitle('Switch to Light Mode')).toBeInTheDocument();

            fireEvent.click(screen.getByRole('button'));
            expect(screen.getByTitle('Switch to Dark Mode')).toBeInTheDocument();
            expect(html.classList.contains('dark')).toBe(false);
            expect(uncaught).not.toHaveBeenCalled();
        } finally {
            window.removeEventListener('error', uncaught);
        }
    });
});

// The toggle mounts only after the session check has answered, so its effect
// cannot be what themes the loading screen before that. A script in
// index.html's <head> sets the class first. It is plain JavaScript that
// repeats initialTheme()'s choice, and these tests are what keep the two the
// same.
describe('the theme script in index.html', () => {
    const page = new DOMParser().parseFromString(
        readFileSync(join(__dirname, '..', '..', '..', 'index.html'), 'utf8'),
        'text/html',
    );
    const inlineScripts = [...page.querySelectorAll('script:not([src])')];

    /** Runs the script the way a browser does on reaching it. */
    function boot() {
        new Function(inlineScripts[0].textContent!)();
    }

    it('is one blocking script in the head, ahead of the application', () => {
        expect(inlineScripts).toHaveLength(1);
        const [script] = inlineScripts;
        expect(script.parentElement).toBe(page.head);
        // `type="module"` would defer it past the first paint, and a `src`
        // is excluded by the selector. It needs no other attribute.
        expect(script.getAttributeNames()).toEqual([]);

        const app = page.querySelector('script[src]')!;
        expect(script.compareDocumentPosition(app) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it.each([
        { stored: 'dark', prefersDark: false, dark: true },
        { stored: 'dark', prefersDark: true, dark: true },
        { stored: 'light', prefersDark: true, dark: false },
        { stored: 'light', prefersDark: false, dark: false },
        { stored: null, prefersDark: true, dark: true },
        { stored: null, prefersDark: false, dark: false },
        // Not a theme this application ever wrote: the system decides.
        { stored: 'banana', prefersDark: true, dark: true },
        { stored: 'banana', prefersDark: false, dark: false },
        { stored: '', prefersDark: true, dark: true },
        { stored: 'DARK', prefersDark: false, dark: false },
    ])('stored $stored, system dark $prefersDark: dark is $dark, and the toggle agrees', ({ stored, prefersDark, dark }) => {
        if (stored !== null) localStorage.setItem('theme', stored);
        systemPrefersDark(prefersDark);

        boot();
        expect(html.classList.contains('dark')).toBe(dark);

        // The toggle's own effect then runs on mount. If the two disagreed,
        // the page would change theme a moment after loading.
        render(<ThemeToggle />);
        expect(html.classList.contains('dark')).toBe(dark);
        expect(screen.getByTitle(`Switch to ${dark ? 'Light' : 'Dark'} Mode`)).toBeInTheDocument();
    });

    it.each([true, false])('follows the system (dark: %s) when storage is blocked, without throwing', (prefersDark) => {
        systemPrefersDark(prefersDark);
        blockStorage();

        expect(boot).not.toThrow();
        expect(html.classList.contains('dark')).toBe(prefersDark);

        render(<ThemeToggle />);
        expect(html.classList.contains('dark')).toBe(prefersDark);
    });

    it('stores nothing', () => {
        systemPrefersDark(true);
        boot();
        expect(localStorage.length).toBe(0);
    });

    // The dev server delivers index.css from JavaScript, so until the
    // application script has loaded the class alone colours nothing. One
    // inline rule covers that gap, and it repeats a design token.
    it('carries the dark background inline, equal to the token in index.css', () => {
        const css = readFileSync(join(__dirname, '..', '..', 'index.css'), 'utf8');
        const token = /\.dark\s*\{[^}]*?--background:\s*([^;]+);/.exec(css)![1].trim();

        const styles = [...page.head.querySelectorAll('style')];
        expect(styles).toHaveLength(1);
        expect(styles[0].textContent!.replace(/\s+/g, ' ').trim())
            .toBe(`html.dark { background-color: hsl(${token}); }`);
    });
});

// The class only changes this application's own colours. `color-scheme` is
// what tells the browser to draw its own parts to match: scrollbars, and any
// form control the application gives no background. Without it a dark page
// had white native inputs holding near-white text. jsdom applies no
// stylesheet, so this reads the declarations; a browser check measured the
// effect.
describe('the colour scheme in index.css', () => {
    const css = readFileSync(join(__dirname, '..', '..', 'index.css'), 'utf8');
    const block = (selector: string) =>
        new RegExp(`^  ${selector} \\{([^}]*)\\}`, 'm').exec(css)![1];

    it('declares light by default and dark under the dark class', () => {
        expect(block(':root')).toMatch(/^\s*color-scheme: light;$/m);
        expect(block('\\.dark')).toMatch(/^\s*color-scheme: dark;$/m);
    });
});
