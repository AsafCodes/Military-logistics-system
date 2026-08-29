/**
 * SEC-H10. hasSystem/hasAnywhere are what a route guard or a render
 * condition calls directly, so they must never THROW -- a throw here would
 * reproduce SEC-H9's blank-app failure in a new place, just moved from a
 * cached user object to a capabilities response. useCapabilities() outside a
 * provider must deny, not grant, since a missing provider is a bug the app
 * should fail closed against rather than reward.
 */
import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';
import { CAPABILITY, hasSystem, hasAnywhere, useCapabilities } from './capabilities';

const GRANTED = { system: ['MANAGE_PERSONNEL'], anywhere: ['TRANSFER'] };

describe('hasSystem / hasAnywhere', () => {
    it('finds a verb that is present', () => {
        expect(hasSystem(GRANTED, CAPABILITY.MANAGE_PERSONNEL)).toBe(true);
        expect(hasAnywhere(GRANTED, CAPABILITY.TRANSFER)).toBe(true);
    });

    it('refuses a verb that is absent, without crossing lists', () => {
        expect(hasSystem(GRANTED, CAPABILITY.MANAGE_CATALOG)).toBe(false);
        // TRANSFER is held `anywhere`, not `system` -- the two lists answer
        // different questions and must not be treated as interchangeable.
        expect(hasSystem(GRANTED, CAPABILITY.TRANSFER)).toBe(false);
        expect(hasAnywhere(GRANTED, CAPABILITY.MANAGE_PERSONNEL)).toBe(false);
    });

    describe('hostile payloads deny rather than throw', () => {
        const HOSTILE = [
            ['null', null],
            ['undefined', undefined],
            ['a missing key', {}],
            ['a non-array system', { system: 'MANAGE_PERSONNEL', anywhere: [] }],
            ['a non-array anywhere', { system: [], anywhere: 'TRANSFER' }],
            ['a plain string in place of the object', 'MANAGE_PERSONNEL'],
        ] as const;

        it.each(HOSTILE)('%s', (_label, value) => {
            // @ts-expect-error -- deliberately hostile input, not a valid Capabilities
            expect(hasSystem(value, CAPABILITY.MANAGE_PERSONNEL)).toBe(false);
            // @ts-expect-error -- deliberately hostile input, not a valid Capabilities
            expect(hasAnywhere(value, CAPABILITY.TRANSFER)).toBe(false);
        });
    });
});

describe('useCapabilities', () => {
    it('denies -- both lists empty -- when rendered outside a provider', () => {
        // No CapabilitiesContext.Provider wraps this hook. A missing provider
        // must never silently grant.
        const { result } = renderHook(() => useCapabilities());
        expect(result.current).toEqual({ system: [], anywhere: [] });
    });
});
