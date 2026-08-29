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
    // author to trust it gets a `tsc -b` failure from the CI build gate.
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    // Restores spies between tests once, centrally, instead of every file
    // remembering a restoreAllMocks hook -- including files not written yet.
    //
    // CAVEAT, and it bites silently: this also strips the implementation off a
    // `vi.fn()` created inside a `vi.mock(...)` FACTORY, from the first test
    // onward. Such a mock then returns undefined and the tests around it can
    // keep passing while the mocked module is quietly broken. Write module
    // factories with plain functions -- `() => Promise.resolve(x)` -- and keep
    // vi.fn() for spies you actually assert on.
    restoreMocks: true,
  },
})
