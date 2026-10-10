/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from "path"

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: true,       // Enable access from outside container (0.0.0.0)
    port: 3000,       // Force port 3000
    strictPort: true, // Fail if port is occupied
    watch: {
      usePolling: true, // Critical for Windows+Docker hot reload
    },
  },
  test: {
    // jsdom, not node: these tests assert on localStorage and on rendered DOM.
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    // Scoped to the source tree. Every test file imports its helpers from
    // 'vitest' explicitly, so `globals` is deliberately off -- switching it on
    // would advertise a convention no file here follows, and the globals would
    // be untyped besides (no tsconfig carries vitest/globals), so the first
    // author to trust it gets a `tsc -b` failure from the CI typecheck gate.
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    // Two settings, one job: no test sees a `vi.spyOn` or a `vi.fn()` as an
    // earlier test left it, without every file remembering a hook -- including
    // files not written yet. `restoreMocks` puts back what `vi.spyOn`
    // replaced. `mockReset` empties the call history of every `vi.fn()` and
    // drops what a test configured on it. Vitest 3 did both under
    // `restoreMocks` alone; Vitest 4 split them (FE-H8-2), and without
    // `mockReset` a `vi.fn()` shared by two tests lets `toHaveBeenCalled()`
    // pass on the earlier test's call. src/test/mockIsolation.test.ts pins
    // both. Neither undoes `vi.stubEnv`, `vi.stubGlobal` or fake timers: a
    // file that uses those still needs its own hook.
    //
    // CAVEAT, and it bites silently: `mockReset` also strips what was
    // configured on a `vi.fn()` created inside a `vi.mock(...)` FACTORY --
    // `vi.fn().mockResolvedValue(x)` -- before every test that starts after
    // the factory has run. For a module the test file imports at the top,
    // that is every test, the first included. Such a mock then returns
    // undefined and the tests around it can keep passing while the mocked
    // module is quietly broken. An implementation passed as the argument,
    // `vi.fn(() => x)`, survives. Write module factories with plain functions
    // -- `() => Promise.resolve(x)` -- and keep vi.fn() for spies you
    // actually assert on.
    restoreMocks: true,
    mockReset: true,
  },
})
