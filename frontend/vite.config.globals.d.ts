// Read only by tsconfig.node.json, which typechecks vite.config.ts against
// Node's globals (lib ES2023, no DOM).
//
// vite's own types name the DOM `Worker` (types/importGlob.d.ts: the `worker`
// key of KnownAsTypeMap, which the deprecated `as: 'worker'` glob option
// selects), so with library checking on the node config fails with TS2304
// until the name exists. Only app code ever reaches that key, and the app
// config has the real DOM type.
//
// This declares the TYPE and nothing else. Adding "DOM" to the node config's
// lib would fix the same error, but it would also let `document`, `window` and
// `new Worker()` typecheck in a file that runs in Node; with this, all three
// still fail. If DOM is ever added there, the two interfaces merge harmlessly.

// eslint-disable-next-line @typescript-eslint/no-empty-object-type -- a type-only name for vite's declarations, deliberately without members
interface Worker {}
