/**
 * FE-H8-2. Pins what `restoreMocks` and `mockReset` in vite.config.ts do
 * together: no test sees a mock as an earlier test left it.
 *
 * Vitest 3 gave all of this under `restoreMocks` alone. Vitest 4 kept only the
 * `vi.spyOn` half there, and the suite stayed green through the upgrade, since
 * no other file shares a `vi.fn()` between tests today. This file is what goes
 * red if either setting is dropped.
 *
 * Each pair runs in order, in one file: the first test leaves a mark, the
 * second looks for it. Run alone, a second test passes without proving
 * anything.
 */
import { describe, it, expect, vi } from 'vitest';
import { answer } from './mockIsolation.fixture';

// The shape vite.config.ts warns about: configured in a module factory.
vi.mock('./mockIsolation.fixture', () => ({
    answer: vi.fn().mockReturnValue('configured in the factory'),
}));

const shared = vi.fn();
const withImplementation = vi.fn(() => 'given as the argument');
const target = { method: () => 'real' };

describe('a vi.fn() shared by two tests (mockReset)', () => {
    it('is called and configured by the first', () => {
        shared.mockReturnValue('configured by the first test');

        expect(shared()).toBe('configured by the first test');
        expect(withImplementation()).toBe('given as the argument');
        expect(shared).toHaveBeenCalledTimes(1);
        expect(withImplementation).toHaveBeenCalledTimes(1);
    });

    it('reaches the second with no calls and nothing configured', () => {
        expect(shared).not.toHaveBeenCalled();
        expect(withImplementation).not.toHaveBeenCalled();
        expect(shared()).toBeUndefined();
    });

    it('keeps an implementation passed as the argument', () => {
        expect(withImplementation()).toBe('given as the argument');
    });
});

describe('a vi.fn() configured in a vi.mock factory (mockReset)', () => {
    // The caveat in vite.config.ts, measured: the mock is there, what the
    // factory configured on it is not. The fixture is imported at the top of
    // this file, so the factory ran before any test here did.
    it('has lost what the factory configured', () => {
        expect(vi.isMockFunction(answer)).toBe(true);
        expect(answer()).toBeUndefined();
    });
});

describe('a vi.spyOn left in place by a test (restoreMocks)', () => {
    it('replaces the method in the first', () => {
        vi.spyOn(target, 'method').mockReturnValue('spied');

        expect(target.method()).toBe('spied');
    });

    it('is undone before the second', () => {
        expect(vi.isMockFunction(target.method)).toBe(false);
        expect(target.method()).toBe('real');
    });
});
