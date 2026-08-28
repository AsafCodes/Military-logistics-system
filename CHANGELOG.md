# Changelog

All notable changes to this project are documented here.

This project uses [Semantic Versioning](https://semver.org/). Pre-production
releases use `0.x.y`; the first production release will be `1.0.0`.

---

## [0.5.0] — 2026-08-28

**The access-control release.** `0.3.0` shipped a security model that was
elaborately modelled, seeded, documented, displayed in the UI — and written by
no application code path. This release replaces it, closes every Critical
finding from the system audit, and puts the project's first test suite and CI
pipeline behind them.

> **Breaking:** database schema, API surface, and the authorization model all
> change. There is no in-place upgrade path from `0.3.0` other than the
> included Alembic migrations, which are one-way for permission *data* — see
> *Migrating* below.

### Architecture — the group access model

The materialized-path hierarchy (`unit_hierarchy`, `unit_path`, `battalion`,
`company`) and the 18-boolean `Profile` permission matrix are both **gone**,
replaced by a single relation:

> `may(P, C, R)` — may principal `P` exercise capability `C` over a resource
> in group `R` — answered by asking whether `R` lies in `extent(P, C)`, the
> union of the subtrees under `P`'s grants carrying `C`.

**New primitives** (`backend/authz.py`):

| Concept | Table | Meaning |
|---|---|---|
| `Group` | `groups` | A node in the org DAG, polymorphic on `kind` |
| `GroupEdge` | `group_edges` | Direct containment: parent contains child |
| `GroupClosure` | `group_closure` | Materialised transitive closure, depth-0 self-rows |
| `GroupMembership` | `group_memberships` | A user is **in** a group |
| `Grant` | `grants` | A user holds a **capability over** a group |

**Membership is not a grant.** A private is a member of their company and
commands none of it. That distinction is the whole point of the model, and it
is the one the old `role` column could not express.

**Seven capabilities**, each read by a router, none declared ahead of its gate:
`VIEW`, `TRANSFER`, `CREATE_EQUIPMENT`, `REPORT_STATUS`, `RESOLVE_FAULT`,
`MANAGE_CATALOG`, `MANAGE_PERSONNEL`.

**Properties this buys, which the old model could not state:**

- **Authority is positional and points down.** The same grant means "the entire
  force" on the root and "one company" on a leaf. The old ladder of visibility
  booleans collapses into *where a single grant sits*.
- **MASTER is no longer special anywhere in the backend.** `is_master` is
  deleted; no router compares a role. A master's reach is a `VIEW` grant on the
  root — so a master is now *bounded by the graph* and cannot see a
  disconnected tree nobody granted them. That is a deliberate narrowing.
- **Two verbs are global.** `MANAGE_CATALOG` and `MANAGE_PERSONNEL` are asked
  over *every* root, so holding them on a node below the top authorises
  nothing, and an empty graph authorises nobody.
- **Possession is a second, independent arm — and it is not everywhere.** An
  item's holder may report a fault on it with no grant, because you can always
  speak about what you are carrying. Closing a fault is authority, so
  `fix_equipment` deliberately has no possession arm: a soldier holding a
  broken item may report it and may not declare it fixed.
- **404 before 403, everywhere.** Every gated route resolves the item inside
  the caller's own VIEW extent *first*, then checks the capability. Run the
  other way round, a 403 confirms an id the caller was never allowed to know
  existed.

### Security — fixed

**Critical**

- **SEC-C1** — Unauthenticated registration granted the first caller MASTER.
  On any fresh or wiped deployment, the first anonymous caller on the network
  became the administrator. The route now requires `MANAGE_PERSONNEL`; the
  count-based role branch is deleted; the first administrator is created
  out-of-band via `backend/bootstrap_admin.py`, which is not reachable over
  HTTP.
- **SEC-C2** — The system-initialization endpoint was unauthenticated and
  seeded the privileged profile, completing the SEC-C1 takeover chain. Removed
  entirely.
- **SEC-C3** — Any authenticated user could set any equipment to any status by
  iterating IDs, with the status written as a free-form string. Now resolved
  through the scoped lookup, gated, and constrained to an enumerated type.
- **SEC-C4** — Every seeded account, including MASTER, shared one hardcoded
  password. Passwords are now generated per account at seed time and printed
  once; seeding refuses to run outside an explicitly flagged local environment.

**High**

- **SEC-H1** — *The headline defect.* The hierarchy field every scoping query
  read was populated only by the seed script, so for all real data the field
  was null, prefix matching excluded the row, and equipment created through the
  API was invisible to everyone but MASTER. Replaced wholesale by the group
  model above, executed as a 13-entry sub-project.
- **SEC-H2** — Unanchored prefix matching leaked across sibling units: a
  single-digit battalion path also matched every multi-digit sibling sharing
  that prefix. There is no prefix matching left to anchor.
- **SEC-H3** — A hardcoded allowlist of profile *names* overrode the permission
  matrix — and one profile in that list was seeded with the corresponding
  permission set to `false`, so a deliberate denial was silently overridden by
  a string comparison. The allowlist is gone.
- **SEC-H4** — Six declared permissions were enforced by no route: a security
  matrix documented, seeded, and displayed in the UI, but not enforced.
  `Profile` and all 18 booleans are deleted along with the `profiles` table.
- **SEC-H5** — Four endpoints applied no scoping whatsoever, leaking total
  force inventory, readiness posture, logistics movements, and the personnel
  directory (including military ID numbers) to any authenticated private. All
  four now scope through shared helpers.
- **SEC-H6** — IDOR on all six equipment write paths. All six now resolve
  through `get_scoped_equipment_or_404` and then gate.
- **SEC-H7** — Equipment creation checked nothing beyond authentication. Now
  gated on `CREATE_EQUIPMENT`.
- **SEC-H8** — Verification routes skipped the active-duty check, so
  discharged personnel retained full access. Both halves fixed: the routes
  moved to the active-duty dependency, **and** login now refuses an inactive
  account outright — reusing the wrong-password 401 verbatim, so the form is
  not an oracle for which military IDs have been deactivated.

**Medium / Low**

- **SEC-M1** — Role promotion accepted an unvalidated free-form string, and
  nothing prevented demoting the last administrator. The route and the column
  are both deleted; authority is grants, revocable and auditable.
- **SEC-L3** — The token data model carried a `role` field that was never
  populated. Deleted.

### Correctness & data integrity — fixed

- **DATA-C1** — *No migration tooling existed.* The schema was created by
  `create_all()` at import, which only ever creates **missing tables** and
  never alters an existing one — making every schema fix in the audit
  unshippable. Alembic is adopted; `run_migrations()` runs at startup, at seed
  time, and in the bootstrap. A pre-Alembic database is stamped automatically
  if it matches, and refused with a diff if it is behind.
- **DATA-C2** — The seed script ran a raw cascading schema drop at the top of
  the routine, against whichever database the environment pointed at, with no
  guard. Now requires `SEED_ENABLED=1`, refuses any non-local `DATABASE_URL`
  (checking the *target*, not the intent), and separates `--reset` from
  ordinary seeding.
- **DATA-H6** — A not-found error was raised inside a `try` and flattened by a
  broad catch into a 500 with the original message embedded.
- **DATA-H9** — ~26 lines of security-critical scoping logic existed verbatim
  in two routers and had **already diverged** at one line, giving two different
  answers to the same authorization question. Extracted to one shared helper.
- **DATA-M15 / M16 / M17 / L1** — The dead role type, the three overlapping
  hierarchy representations, the duplicated permission column, and the walrus
  assignment in a class base-class position are all deleted.

### Infrastructure

- **INF-C1** — The database was published on every host interface with
  placeholder-grade credentials hardcoded in a tracked file. Port publication
  removed; all credentials interpolated from the environment; `.env.example`
  restored.
- **INF-C2** — CI restored (backend + frontend), having been removed by an
  over-broad revert and never restored.
- **INF-C3** — **The test suite, which did not exist.** 704 lines were
  destroyed by that same revert. Restored and extended: **306 tests** now
  cover the closure engine, every capability gate, listing scope, the
  migration round-trip, seed graph integrity, and edge cases.

### API changes

| Removed | Replaced by |
|---|---|
| `PUT /users/promote` | *(nothing — roles are gone; authority is grants)* |
| `GET /profiles` | `GET /groups` (gated on `MANAGE_PERSONNEL`) |
| `POST /setup/initialize_system` | `backend/bootstrap_admin.py` (out-of-band) |
| *(none)* | `PUT /users/{user_id}/group` (gated on `MANAGE_PERSONNEL`) |

`POST /users/` now **requires** `group_id` and places the account in the same
transaction that creates it. Previously it issued no membership at all, so a
fresh account was a member of nothing and invisible to everyone including the
administrator who had just created it.

`UserResponse` drops `role`, `battalion`, `company`, and the embedded
`profile` permission matrix; it gains `group`.

### Migrating from 0.3.0

Run `alembic upgrade head` (or start the app, which runs it). Three migrations
apply in order: group tables → drop legacy hierarchy columns → retire
`Profile`/`UserRole`.

- **Equipment placement is preserved.** The hierarchy migration backfills
  `equipment.group_id` from the path strings *before* destroying them, and
  **refuses to run** rather than strand an item in no group.
- **Permission data is not preserved.** The `profiles` table is dropped with
  no backfill — there is no successor column to feed, because the 18 booleans
  became grants at the model level, not at the row level. `downgrade()`
  restores the table *shape* only. **Re-issue grants after upgrading.**

### Known limitations

Carried forward deliberately, tracked in `TODO.md`:

- **SEC-H10** — Client-side capability gates were *removed* rather than
  corrected, because the frontend has no way to ask what the caller may do.
  Every control now renders for every user and the backend refuses on click.
  This closes the previous *disagreement* (where the UI both offered actions
  the backend refused and hid actions it would permit) but a capabilities
  endpoint is the real fix.
- **SEC-M2** — `is_active_duty` is still client-controlled at account creation.
- **API-H6** — The fault-type approval workflow has no caller, so pending
  fault types have no way out of the queue.
- **SEC-M12** — `MANAGE_CATALOG` folds catalog *addition* and *removal* into
  one verb. Safe only while their holders coincide, which they currently do.

---

## [0.3.0] — 2026-02-15

Modular architecture (routers), Orbital login, AppShell, feature-based
frontend, Docker compose, Verification & Status History.

## [0.2.0]

Maintenance tickets, reports, daily verification, Matrix Security.

## [0.1.0]

Initial system: equipment CRUD, basic auth, dashboard.
