import { describe, expect, it } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import AppShell from './AppShell';
import { TEST_CAPABILITIES, TEST_USER, withCapabilities } from '@/test/setup';

// FE-H6: the mobile menu closes on a route change while rendering, not from
// an effect. These tests pin when it is open and everything that closes it;
// they pass for either mechanism, and lint is what tells the two apart.

/** Page content that changes the route without touching the menu. */
function Page() {
    const navigate = useNavigate();
    const location = useLocation();
    return (
        <>
            <output data-testid="url">{location.pathname + location.search}</output>
            <button onClick={() => navigate('/reports')}>go to reports</button>
            <button onClick={() => navigate('/reports?tab=2')}>same path, new query</button>
            <button onClick={() => navigate(-1)}>back</button>
        </>
    );
}

const url = () => screen.getByTestId('url').textContent;

function renderShell(strict = false, entries = ['/dashboard']) {
    return render(
        <MemoryRouter initialEntries={entries} initialIndex={entries.length - 1}>
            {withCapabilities(
                <AppShell user={TEST_USER} onLogout={() => { }}>
                    <Page />
                </AppShell>,
                TEST_CAPABILITIES,
            )}
        </MemoryRouter>,
        { reactStrictMode: strict },
    );
}

const overlay = (c: HTMLElement) => c.querySelector('.fixed.inset-0.z-40');
const sidebar = (c: HTMLElement) => c.querySelector('aside')!;
const iconButton = (c: HTMLElement, icon: string) => c.querySelector(`.lucide-${icon}`)!.closest('button')!;

function isOpen(c: HTMLElement) {
    const slidIn = !sidebar(c).className.includes('translate-x-full');
    // The overlay and the slide class come from one flag; a test that saw
    // them disagree would be reading a half-applied state.
    expect(overlay(c) !== null).toBe(slidIn);
    return slidIn;
}

const openMenu = (c: HTMLElement) => fireEvent.click(iconButton(c, 'menu'));

function resizeTo(width: number) {
    act(() => {
        window.innerWidth = width;
        window.dispatchEvent(new Event('resize'));
    });
}

describe('AppShell mobile menu', () => {
    it('starts closed and opens from the hamburger', () => {
        const { container } = renderShell();
        expect(isOpen(container)).toBe(false);

        openMenu(container);
        expect(isOpen(container)).toBe(true);
    });

    it('closes when a navigation item changes the route', () => {
        const { container } = renderShell();
        openMenu(container);

        fireEvent.click(screen.getByRole('button', { name: 'ציוד' }));
        expect(isOpen(container)).toBe(false);
    });

    it('closes when the page itself changes the route', () => {
        const { container } = renderShell();
        openMenu(container);

        fireEvent.click(screen.getByText('go to reports'));
        expect(isOpen(container)).toBe(false);
    });

    it('stays closed when Back returns to the path it was opened on', () => {
        const { container } = renderShell();
        openMenu(container);
        fireEvent.click(screen.getByText('go to reports'));

        fireEvent.click(screen.getByText('back'));
        expect(isOpen(container)).toBe(false);
    });

    it('opens again after a route change closed it, and closes again on the next', () => {
        const { container } = renderShell();
        openMenu(container);
        fireEvent.click(screen.getByText('go to reports'));

        openMenu(container);
        expect(isOpen(container)).toBe(true);

        fireEvent.click(screen.getByText('back'));
        expect(isOpen(container)).toBe(false);
    });

    it('stays open when only the query string changes', () => {
        const { container } = renderShell();
        fireEvent.click(screen.getByText('go to reports'));
        openMenu(container);

        fireEvent.click(screen.getByText('same path, new query'));
        expect(isOpen(container)).toBe(true);
    });

    // Picking an item is itself what closes the menu. The route does not
    // change here, so nothing that watches the route would close it.
    it('closes when the current page is picked again', () => {
        const { container } = renderShell();
        openMenu(container);

        fireEvent.click(screen.getByRole('button', { name: 'לוח בקרה' }));
        expect(isOpen(container)).toBe(false);
        expect(url()).toBe('/dashboard');
    });

    it('adds no history entry when the current page is picked again', () => {
        const { container } = renderShell();
        fireEvent.click(screen.getByText('go to reports'));
        openMenu(container);

        fireEvent.click(screen.getByRole('button', { name: 'דוחות' }));
        expect(url()).toBe('/reports');

        // One Back leaves the page. A second entry for /reports would have
        // kept it here.
        fireEvent.click(screen.getByText('back'));
        expect(url()).toBe('/dashboard');
    });

    it('drops a query string when the current page is picked, replacing that entry', () => {
        const { container } = renderShell();
        fireEvent.click(screen.getByText('go to reports'));
        fireEvent.click(screen.getByText('same path, new query'));
        expect(url()).toBe('/reports?tab=2');
        openMenu(container);

        fireEvent.click(screen.getByRole('button', { name: 'דוחות' }));
        expect(url()).toBe('/reports');
        expect(isOpen(container)).toBe(false);

        // History was /dashboard, /reports, /reports?tab=2 and the last
        // entry is the one replaced, so Back lands on the second.
        fireEvent.click(screen.getByText('back'));
        expect(url()).toBe('/reports');
        fireEvent.click(screen.getByText('back'));
        expect(url()).toBe('/dashboard');
    });

    // The router shows the dashboard for these typed addresses too, so a
    // pick from them is a pick of the current page.
    it.each(['/dashboard/', '/Dashboard', '/dashboard#top'])(
        'adds no history entry when the current page was reached as %s',
        (typed) => {
            const { container } = renderShell(false, ['/reports', typed]);
            openMenu(container);

            fireEvent.click(screen.getByRole('button', { name: 'לוח בקרה' }));
            expect(url()).toBe('/dashboard');
            expect(isOpen(container)).toBe(false);

            fireEvent.click(screen.getByText('back'));
            expect(url()).toBe('/reports');
        },
    );

    it('still adds a history entry when another page is picked', () => {
        const { container } = renderShell();
        openMenu(container);

        fireEvent.click(screen.getByRole('button', { name: 'ציוד' }));
        expect(url()).toBe('/equipment');

        fireEvent.click(screen.getByText('back'));
        expect(url()).toBe('/dashboard');
    });

    it('closes from the overlay and from the close button', () => {
        const { container } = renderShell();

        openMenu(container);
        fireEvent.click(overlay(container)!);
        expect(isOpen(container)).toBe(false);

        openMenu(container);
        fireEvent.click(iconButton(container, 'x'));
        expect(isOpen(container)).toBe(false);
    });

    it('closes when the window grows past the mobile breakpoint, not below it', () => {
        const { container } = renderShell();
        openMenu(container);

        resizeTo(767);
        expect(isOpen(container)).toBe(true);

        resizeTo(768);
        expect(isOpen(container)).toBe(false);
    });

    it('behaves the same under StrictMode', () => {
        const { container } = renderShell(true);
        openMenu(container);
        expect(isOpen(container)).toBe(true);

        fireEvent.click(screen.getByText('go to reports'));
        expect(isOpen(container)).toBe(false);

        openMenu(container);
        expect(isOpen(container)).toBe(true);

        fireEvent.click(screen.getByRole('button', { name: 'דוחות' }));
        expect(isOpen(container)).toBe(false);
    });
});
