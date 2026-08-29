/**
 * localStorage that cannot take the application down.
 *
 * Where a browser refuses site data -- blocked by policy or by the user, a
 * sandboxed iframe, some private-browsing modes -- it is the `localStorage`
 * ACCESSOR that throws, not the method call. So `localStorage.getItem(k)`
 * throws before `getItem` is ever reached, and wrapping the parse of the result
 * protects nothing.
 *
 * That matters more than it sounds. Every one of these call sites runs during
 * boot or during a mount effect, where a throw is not a failed read -- it is a
 * failed render, which unmounts the tree and leaves the operator on a blank
 * page or a crash screen with no way back. SEC-H9 exists because exactly that
 * happened via an unguarded cached-user parse.
 *
 * Routing every call site through here is a convention, and a convention that
 * only a comment defends is one the next file breaks. `safeStorage.test.ts`
 * therefore scans the source tree and fails on any bare `localStorage` outside
 * this module -- the same trick `test_no_get_route_changes_state` uses on the
 * backend, and a hard CI gate as of this ticket.
 *
 * Storage is a convenience here (a remembered theme, a one-time cleanup), never
 * the source of truth for anything -- the session lives in an httpOnly cookie
 * and identity comes from the server. So failing quietly is right: there is
 * nothing a caller could usefully do about it.
 */

export function readLocal(key: string): string | null {
    try {
        return localStorage.getItem(key);
    } catch {
        return null;
    }
}

export function writeLocal(key: string, value: string): void {
    try {
        localStorage.setItem(key, value);
    } catch {
        // Also the quota-exceeded path, which throws on write even where
        // reading is permitted.
    }
}

export function removeLocal(key: string): void {
    try {
        localStorage.removeItem(key);
    } catch {
        // Nothing readable means nothing to remove.
    }
}
