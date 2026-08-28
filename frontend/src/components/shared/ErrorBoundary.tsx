import { Component, type ErrorInfo, type ReactNode } from 'react';

/**
 * SEC-H9. The application had no error boundary anywhere, so any throw during
 * render unmounted the whole tree and left an operator staring at a blank white
 * page with no indication anything had gone wrong.
 *
 * Written as a class rather than pulling in `react-error-boundary`: catching is
 * only possible in a class component, this needs no configuration, and a
 * dependency added to a legacy tree is one the rewrite has to carry.
 *
 * The fallback deliberately uses plain markup and utility classes instead of
 * the shared `<Button>` and friends. This is the last thing standing between a
 * failed render and a white screen, so it should not import the component layer
 * that may be what just threw. The cost is a button that will not track the
 * design system; that is the intended trade, not an oversight.
 *
 * Worth knowing what this does NOT catch, so nobody mistakes it for a net:
 * React boundaries see errors thrown during render, in lifecycle methods, and
 * in constructors below them. They do not see errors from event handlers,
 * `setTimeout`, or rejected promises. The malformed-cache bug this shipped
 * alongside was caught because it threw synchronously inside an effect body --
 * had it been inside the `.then` it would have sailed straight past.
 */

interface Props {
    children: ReactNode;
}

interface State {
    error: Error | null;
}

class ErrorBoundary extends Component<Props, State> {
    state: State = { error: null };

    static getDerivedStateFromError(error: Error): State {
        return { error };
    }

    componentDidCatch(error: Error, errorInfo: ErrorInfo) {
        // Console is the only sink this project has (DATA-M20 tracks the
        // absence of a logging framework). Better here than swallowed.
        console.error('Unhandled render error:', error, errorInfo.componentStack);
    }

    handleReload = () => {
        window.location.reload();
    };

    render() {
        if (this.state.error) {
            return (
                <div className="min-h-screen flex items-center justify-center bg-background p-6">
                    <div className="max-w-md w-full space-y-4 text-center">
                        <h1 className="text-xl font-semibold text-foreground">
                            Something went wrong
                        </h1>
                        <p className="text-sm text-muted-foreground">
                            The page could not be displayed. Reloading usually clears this.
                            If it keeps happening, report it with the time it occurred.
                        </p>
                        <button
                            type="button"
                            onClick={this.handleReload}
                            className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium"
                        >
                            Reload
                        </button>
                    </div>
                </div>
            );
        }

        return this.props.children;
    }
}

export default ErrorBoundary;
