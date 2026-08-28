/**
 * SEC-H9's third clause: there was no error boundary anywhere in the tree, so
 * any throw during render unmounted everything and left a blank white page.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import ErrorBoundary from './ErrorBoundary';

function Boom(): React.ReactNode {
    throw new Error('render exploded');
}

describe('ErrorBoundary', () => {
    // React logs every caught error to console.error regardless, so a passing
    // run would otherwise read as a failing one. Restored centrally.
    let logged: ReturnType<typeof vi.spyOn>;
    beforeEach(() => {
        logged = vi.spyOn(console, 'error').mockImplementation(() => { });
    });

    it('renders children when nothing throws', () => {
        render(
            <ErrorBoundary>
                <p>all is well</p>
            </ErrorBoundary>,
        );

        expect(screen.getByText('all is well')).toBeInTheDocument();
    });

    it('shows a fallback instead of a blank page when a child throws', () => {
        const { container } = render(
            <ErrorBoundary>
                <Boom />
            </ErrorBoundary>,
        );

        expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
        // The actual regression -- the old behaviour was an empty tree.
        expect(container).not.toBeEmptyDOMElement();
    });

    it('offers the user a way out', () => {
        render(
            <ErrorBoundary>
                <Boom />
            </ErrorBoundary>,
        );

        expect(screen.getByRole('button', { name: /reload/i })).toBeInTheDocument();
    });

    it('logs the failure rather than swallowing it', () => {
        render(
            <ErrorBoundary>
                <Boom />
            </ErrorBoundary>,
        );

        // Console is the only sink this project has (DATA-M20).
        expect(
            logged.mock.calls.some(call => String(call[0]).includes('Unhandled render error')),
        ).toBe(true);
    });
});
