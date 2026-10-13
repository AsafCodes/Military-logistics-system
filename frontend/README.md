# Frontend

The browser client of the Military Logistics system: React, TypeScript and Vite, with Tailwind CSS. The [root readme](../README.md) says how to start the whole stack. This file is about working in this folder.

## Commands

In `frontend/`, on Node 20, the version the CI uses:

- Install what `package-lock.json` names: `npm ci`
- Start the development server: `npm run dev`
- Run the linter: `npm run lint`
- Run the typecheck: `npm run typecheck`
- Run the tests: `npm run test`
- Build for production: `npm run build`

The linter, the typecheck and the tests are in [CONTRIBUTING.md](../CONTRIBUTING.md#testing) too, with the CI run that repeats them on a pull request.

## What to know

- The development server answers at `http://localhost:3000`. `vite.config.ts` sets the port with `strictPort`, so the server stops if the port is taken and does not move to another.
- The API's address is `VITE_API_URL`, which Vite reads when it serves or builds. Unset, the client calls `http://localhost:8000`.
- The tests are the `*.test.ts` and `*.test.tsx` files under `src/`. Vitest runs them in jsdom.
