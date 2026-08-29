/**
 * SEC-H10. What the caller may do, and how a component asks.
 *
 * Authority in the backend is positional (backend/authz.py: authz.may depends
 * on which group a resource belongs to), so "what may I do" has two different
 * shapes -- and this file is where that distinction becomes something a
 * component can read instead of something a comment has to keep explaining.
 *
 *   - `system`   is EXACT. One authz.may_global() result per entry -- the
 *     same boolean the routes gating on it already compute. Absence here
 *     means the server WILL refuse. Only a `hasSystem` check may be used as
 *     a route guard.
 *   - `anywhere` is NOT a gate. It says the caller holds the verb over SOME
 *     group, which over-shows (a control may still 403 on a specific item)
 *     and never hides one the caller is entitled to use elsewhere. Fine for
 *     "should this button render at all"; never fine for "is this allowed".
 */
import { createContext, useContext } from 'react';

export const CAPABILITY = {
    MANAGE_PERSONNEL: 'MANAGE_PERSONNEL',
    MANAGE_CATALOG: 'MANAGE_CATALOG',
    VIEW: 'VIEW',
    TRANSFER: 'TRANSFER',
    CREATE_EQUIPMENT: 'CREATE_EQUIPMENT',
    REPORT_STATUS: 'REPORT_STATUS',
    RESOLVE_FAULT: 'RESOLVE_FAULT',
} as const;

export type CapabilityVerb = (typeof CAPABILITY)[keyof typeof CAPABILITY];

export interface Capabilities {
    system: string[];
    anywhere: string[];
}

export const EMPTY_CAPABILITIES: Capabilities = { system: [], anywhere: [] };

// The endpoint is trusted, but a guard that THROWS on a malformed response
// reproduces SEC-H9's blank-app failure in a new place -- these must answer
// `false` for null, a missing key, or a key that isn't an array, not throw.
function includesVerb(list: unknown, verb: CapabilityVerb): boolean {
    return Array.isArray(list) && list.includes(verb);
}

export function hasSystem(caps: Capabilities | null | undefined, verb: CapabilityVerb): boolean {
    return includesVerb(caps?.system, verb);
}

export function hasAnywhere(caps: Capabilities | null | undefined, verb: CapabilityVerb): boolean {
    return includesVerb(caps?.anywhere, verb);
}

// No JSX in this file (it stays a .ts, alongside safeStorage.ts) -- the
// Provider is written at the App.tsx call site.
export const CapabilitiesContext = createContext<Capabilities | null>(null);

// A missing provider must never grant. Returning EMPTY_CAPABILITIES rather
// than throwing means a component rendered outside AuthenticatedLayout by
// mistake sees "no authority" instead of taking the whole tree down.
export function useCapabilities(): Capabilities {
    return useContext(CapabilitiesContext) ?? EMPTY_CAPABILITIES;
}
