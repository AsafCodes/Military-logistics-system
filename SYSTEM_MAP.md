# 🗺️ SYSTEM MAP — Military Logistics ("Marker System")

> **Version:** 0.5.0
> **Last Updated:** 2026-08-28
> **Solo Dev Reference.** Read this before touching anything.

---

## 1. 🌟 The "North Star" (The Goal)

**This software tracks military equipment ownership, location, and operational readiness across a hierarchical unit structure (Brigade → Battalion → Company → Soldier).**

It answers three questions at any moment:
1. **Where is every piece of equipment?** (Who holds it, where is it stored)
2. **Is it functional?** (Maintenance status, fault tickets)
3. **Is it accounted for?** (Daily verification compliance)

---

## 2. 📂 Project Architecture

### Repository Structure

```
Marker_System/
├── backend/                    # FastAPI Python package
│   ├── main.py                 # App entry point, CORS, router registration
│   ├── database.py             # SQLAlchemy engine + session (SQLite/PostgreSQL)
│   ├── models.py               # Core ORM models (11 tables)
│   ├── schemas.py              # All Pydantic request/response schemas
│   ├── security.py             # JWT + password hashing + password generation
│   ├── dependencies.py         # Auth deps + Matrix Security scoping + compliance helper
│   ├── enums.py                # Shared enumerated types (EquipmentStatus)
│   ├── authz.py                # Group algebra: Group, GroupEdge, GroupClosure, GroupMembership, Grant
│   ├── migrations.py           # Alembic runner; replaces create_all
│   ├── bootstrap_admin.py      # Out-of-band initial MASTER (not reachable over HTTP)
│   ├── seed_data.py            # Bulk-insert test data (⚠️ destructive with --reset)
│   └── routers/                # Modular API endpoints
│       ├── auth.py             # POST /login
│       ├── users.py            # CRUD + /users/me + /users/{id}/group
│       ├── equipment.py        # Equipment CRUD + transfer + daily verify
│       ├── maintenance.py      # Fault reporting + ticket management + fix
│       ├── verifications.py    # Detailed condition verification + status history
│       ├── setup.py            # System init + fault type CRUD + groups
│       ├── reports.py          # Inventory query + daily movement
│       └── analytics.py        # Unit readiness stats
├── frontend/                   # React + TypeScript + Vite
│   └── src/
│       ├── App.tsx                          # Root: auth state, routing
│       ├── api.ts                           # Axios instance
│       ├── index.css                        # CSS design tokens (dark/light)
│       ├── r3f.d.ts                         # React Three Fiber type declarations
│       ├── components/
│       │   ├── layout/
│       │   │   └── AppShell.tsx             # Sidebar + top bar + content area
│       │   ├── ui/                          # Shadcn/UI + custom components
│       │   │   ├── button.tsx, card.tsx, form.tsx, input.tsx, label.tsx
│       │   │   ├── NetworkGlobe.tsx         # 3D particle globe (R3F)
│       │   │   ├── ThemeToggle.tsx          # Dark/light toggle
│       │   │   ├── AutocompleteInput.tsx    # Searchable input
│       │   │   └── SearchableMultiSelect.tsx
│       │   └── shared/
│       │       └── ConnectionTest.tsx       # Backend health check widget
│       ├── features/
│       │   ├── auth/
│       │   │   └── components/
│       │   │       ├── LoginPage.tsx        # Orbital login (hero + globe + form)
│       │   │       └── LegacyLogin.tsx      # Old login (kept as reference)
│       │   ├── dashboard/
│       │   │   ├── components/
│       │   │   │   ├── DashboardPage.tsx    # Welcome + stats + equipment preview
│       │   │   │   ├── StatsGrid.tsx        # 4 animated ring stat cards
│       │   │   │   ├── EquipmentTable.tsx   # Top-5 equipment preview table
│       │   │   │   ├── DailyActivityTable.tsx # Recent event feed
│       │   │   │   └── AdminPanel.tsx       # User search, group assignment
│       │   │   └── hooks/                   # Dashboard-specific hooks
│       │   ├── equipment/
│       │   │   └── components/
│       │   │       ├── EquipmentPage.tsx     # Full table + modals (41KB)
│       │   │       ├── EquipmentHistory.tsx  # History modal
│       │   │       └── VerificationForm.tsx  # Condition report form
│       │   ├── maintenance/
│       │   │   └── components/
│       │   │       └── MaintenancePage.tsx   # Ticket management
│       │   └── reports/
│       │       └── components/
│       │           └── GeneralReportPage.tsx # Inventory reports + CSV export
│       ├── services/
│       │   ├── auth.service.ts              # Login/logout/getMe
│       │   ├── equipment.service.ts         # Equipment API calls
│       │   └── reports.service.ts           # Report API calls
│       └── types/
│           └── index.ts                     # TypeScript interfaces
├── docker-compose.yml          # 3-service orchestration (db + backend + frontend)
├── Dockerfile.backend          # Python 3.10 + uvicorn
├── frontend/Dockerfile         # Node frontend container
├── alembic.ini                 # Migration config (URL comes from DATABASE_URL)
├── alembic/                    # Migration environment + versions/
├── requirements.txt            # Python dependencies
├── .env / .env.example         # SECRET_KEY configuration
└── SYSTEM_MAP.md               # ← You are here
```

---

## 3. 🧠 Core Logic Modules (The "Brains")

### Module A: Matrix Security Engine
- **Files:** `dependencies.py` → `scope_equipment_query()` (the cascade itself), `equipment.py` → `get_accessible_equipment()` (caller)
- **Responsibility:** Decides **who sees what** from the group algebra — `extent(user, VIEW)`, the set of groups reachable downward from the user's `VIEW` grants.
- **How it works:** Two arms, OR'd: the item's `group_id` is in your extent, **or** you personally hold it. A grant on a battalion reaches its companies; a grant on a company reaches nothing above it.
- **⚠️ Non-Obvious Detail:** There is no cascade and no first-match-wins any more. The path strings this used to compare (`unit_hierarchy`, `unit_path`) were **dropped in H1-11**; the profile-flag ladder (`can_view_all` → `can_view_battalion` → `can_view_company`) stopped being read at H1-10 and the MASTER bypass was deleted there. This section is a summary — `TODO-SEC-H1.md` is the specification.

### Module B: Compliance Engine
- **Files:** `dependencies.py` → `get_daily_status()`, `models.py` → `Equipment.compliance_level`
- **Responsibility:** Flags equipment as "GOOD" / "WARNING" / "SEVERE" based on time since last verification.
- **Rules:** <24h = GOOD, 24-48h = WARNING, >48h = SEVERE.
- **⚠️ Non-Obvious Detail:** `compliance_level` is a **computed property**, not stored in the DB. It recalculates on every read using `datetime.utcnow()`. You cannot query or filter by it in SQL.

### Module C: Ownership vs. Possession Model
- **Files:** `models.py` → `Equipment` (fields: `owner_user_id`, `holder_user_id`, `custom_location`, `actual_location_id`)
- **Responsibility:** Separates WHO OWNS an item (`owner_user_id`) from WHO HAS IT RIGHT NOW (`holder_user_id`) from WHERE IT IS (`custom_location` / `actual_location_id`).
- **⚠️ Non-Obvious Detail:** Transfer to a person clears location. Transfer to a location clears holder. These are mutually exclusive (XOR validation in `equipment.py`). Don't try to set both at once.

### Module D: Fault & Ticket Pipeline
- **Files:** `routers/maintenance.py`
- **Responsibility:** `report_fault` → creates `FaultType` (if new) → creates `MaintenanceLog` ticket → marks equipment "Malfunctioning". `fix_equipment` → sets "Functional" → closes all open tickets → logs `TransactionLog(event_type="FIX")`.
- **Authority (H1-9):** both writes resolve through `get_scoped_equipment_or_404` and then gate. `report_fault` uses `require_status_authority` (**holder or `REPORT_STATUS`**); `fix_equipment` uses `require(RESOLVE_FAULT)` and deliberately has **no possession arm** — a soldier holding a broken item may report it and may not declare it fixed, and a company commander may report on their company and may not close the ticket.
- **⚠️ Non-Obvious Detail:** whoever creates a new fault type without holding `REPORT_STATUS` over the item's group gets `is_pending=True` — approval is needed before it shows up in the general list. That is now a grant question rather than a profile boolean, and it is the one place `may()` is called instead of `require()`, because a "no" here narrows the write rather than refusing it. Note the approval workflow itself has no caller (API-H6), so pending fault types currently have no way out of the queue.
- **Scoping (H1-10.5):** `GET /tickets/` runs through `dependencies.scope_equipment_derived_query` — a ticket is exactly as visible as the item it is about. The join is **inner**, so a ticket whose `equipment_id` is NULL or dangling disappears rather than rendering as "Unknown"; that is a behaviour change for such rows, not merely a narrowing.

### Module E: Verification & Audit Trail
- **Files:** `routers/verifications.py` (2 sub-routers: `router` + `history_router`)
- **Responsibility:** Records detailed equipment condition reports. If the reported status differs from current status, automatically creates an `EquipmentStatusHistory` entry linked to the verification.
- **Endpoints:** `POST /verifications/` (create), `GET /verifications/equipment/{id}` (list), `GET /equipment/{id}/history` (status changes).
- **Authority (H1-9):** all three resolve through `get_scoped_equipment_or_404`. The write then gates on `require_status_authority` (holder or `REPORT_STATUS`); the two reads gate on **nothing further** — they are reads, so the resolver *is* the VIEW gate. Until H1-9 all three took the raw id, so any authenticated account could read any asset's full observation history and status audit trail by counting.
- **Active duty (H1-10.5):** all three routes are on `get_current_active_user`, and `login_for_access_token` refuses an inactive account outright — the per-route check only declines a credential that was already issued, so the login gate is what closes the class rather than the instances. The refusal reuses the wrong-password 401 verbatim so the form is not an oracle for which military IDs have been deactivated.

### Module F: Profile Permission Matrix ("The Green Table") — **retired at H1-12**
- **Files:** none. `Profile`, `UserRole` and `User.role` are deleted from `models.py`, the `profiles` table is dropped, and `seed_data.py`/`bootstrap_admin.py` no longer construct any of it.
- **Responsibility:** none — this module no longer exists. It used to control what each role could do; the access model (Module F2) took over every one of those decisions at H1-10, and H1-12 removed the now-inert schema and the frontend controls that still read it (`AdminPanel.tsx`'s profile picker, `EquipmentPage.tsx`'s `can_change_assignment_others` gate, `DashboardPage.tsx`'s role label).

### Module F1: Listing Scope
- **Files:** `backend/dependencies.py` → `scope_equipment_query`, `scope_equipment_derived_query`, `scope_user_query`
- **Responsibility:** every list endpoint answers *what may you see*, never *are you an administrator*. Three helpers, and which one applies is decided by what the rows ARE:
  - **equipment** → `scope_equipment_query` (group in `extent(VIEW)`, or you hold it)
  - **rows that refer to equipment** — transaction logs, maintenance tickets → `scope_equipment_derived_query`, which joins `Equipment` and defers to the above
  - **people** → `scope_user_query`: membership in a group inside `extent(VIEW)`, **or** yourself, **or** — for `MANAGE_PERSONNEL` holders — anyone belonging to no group at all
- **⚠️ Non-Obvious Detail:** that third arm on `scope_user_query` is load-bearing, and was found by probing rather than by reading, at a time when `create_user` issued no membership at all — a fresh account was a member of nothing and invisible to **everyone including the master who just created it**. H1-12 closed that at the source: `create_user` now requires a `group_id` and places the account in the same transaction that creates it. The arm stays regardless — a `Group` can still be deleted out from under a member, since `GroupMembership` cascades on the group's `ondelete` — so an unplaced account remains reachable, just rarer.
- **⚠️ Non-Obvious Detail:** `GET /groups` (H1-12 — replaces `GET /profiles`) is *gated* (`MANAGE_PERSONNEL`) rather than scoped, because the personnel table belongs to no unit. That is the same reason `authz.require_global` exists.

### Module F2: The Group Access Model
- **Files:** `backend/authz.py`, `backend/enums.py` → `Capability`, `backend/dependencies.py`
- **Responsibility:** The single relation every access question reduces to. `may(P, C, R)` — may principal `P` exercise capability `C` over a resource in group `R` — answered by asking whether the resource's group lies in `extent(P, C)`, the union of the subtrees under `P`'s grants carrying `C`.
- **Primitives:** `Group` (polymorphic on `kind`), `GroupEdge` (direct containment), `GroupClosure` (materialised transitive closure, `depth` = shortest path, depth-0 self-rows), `GroupMembership` (a user is *in* a group), `Grant` (a user holds a capability *over* a group). **Membership is not a grant** — a private is a member of their company and commands none of it.
- **Capabilities:** `VIEW` (all listings), `TRANSFER` (`assign_owner`, `transfer_equipment`), `CREATE_EQUIPMENT` (`create_equipment`), `REPORT_STATUS` (`report_fault`, `create_verification`), `RESOLVE_FAULT` (`fix_equipment`), `MANAGE_CATALOG` (`setup.py`'s fault-type routes), `MANAGE_PERSONNEL` (`users.py`'s `create_user` and `update_user_group`, plus `setup.list_groups`). Every member is read by a router; none is declared ahead of its gate.
- **⚠️ Non-Obvious Detail:** two of those verbs are **global**. `CatalogItem` and `FaultType` have no group and never will -- they are one namespace shared by every unit -- and neither does the personnel table. `authz.require_global` asks the verb over **every root**, which reads as authority over the whole graph: holding it on a node below the top authorises nothing, and an empty graph authorises nobody (`all()` over an empty sequence would say the opposite). This corrects a claim `Capability`'s docstring carried from H1-7 to H1-9, that a catalog verb was *unenforceable* here. *Has no group* is not *has no place in the graph*.
- **⚠️ Non-Obvious Detail:** **possession is a second, independent arm, and it is not everywhere.** `dependencies.require_status_authority` lets an item's holder report a fault on it without any grant — you can always speak about what you are carrying, the same reasoning `scope_equipment_query` applies to VIEW. `fix_equipment` deliberately does not use it: closing a fault is authority, so a soldier holding a broken item may report it and may not declare it fixed. `verify_equipment_daily` is narrower still — holder only — because it records "I am carrying this" and possession-or-grant would let a commander confirm presence of kit they do not hold.
- **⚠️ Non-Obvious Detail:** a grant over a group the holder cannot **see** authorises nothing, because the resolver asks VIEW first. H1-9 found several profiles in that state and H1-10 resolved it by granting the tech soldiers sight of the unit they maintain -- the rule being *you see what you may maintain*. Still read a grant table together with the VIEW table: the two are independent, and only convention keeps them aligned.
- **⚠️ Non-Obvious Detail:** **MASTER is no longer special anywhere in the backend.** `is_master` is deleted. Its sight is a `VIEW` grant on the root -- issued by `seed_data`, the test fixture and `bootstrap_admin.grant_root_authority` alike -- so a master is bounded by the graph and cannot see a disconnected tree nobody granted them. `models.UserRole` is deleted entirely (H1-12) — there is no longer even a dead column to remain as.
- **⚠️ Non-Obvious Detail:** authority is **positional and points down**. The same grant means "the entire force" on the root group and "one company" on a leaf, which is how the ladder of visibility booleans collapsed into where a single grant sits.
- **⚠️ Non-Obvious Detail:** every gated route runs **404 before 403** — resolve the item inside the caller's own VIEW extent (`get_scoped_equipment_or_404`), *then* `authz.require(...)`. Run the other way round, a 403 confirms an id the caller was never allowed to know existed. Calling `require()` on an id straight from a request body reinstates that oracle.

### Module G: Login Page & 3D Globe ("Orbital" Design)
- **Files:** `features/auth/components/LoginPage.tsx`, `components/ui/NetworkGlobe.tsx`, `components/ui/ThemeToggle.tsx`, `r3f.d.ts`
- **Responsibility:** Full-screen login page with an Orbital-style layout: hero text + inline login form (left 45%), animated 3D particle globe (right 55%), stats bar (bottom), navbar with dark/light theme toggle.
- **How it works:** `NetworkGlobe.tsx` uses React Three Fiber (`@react-three/fiber`) + drei helpers (`Points`, `PointMaterial`) to render 3,000 uniformly-distributed particles on a sphere. A `<torus>` ring orbits the sphere. Colors, particle size, ring opacity, and glow opacity are all **theme-aware**. `ThemeToggle.tsx` toggles `.dark` class on `<html>`, persists to `localStorage`, and `LoginPage.tsx` watches for class changes via `MutationObserver` to pass `isDark` to the globe.
- **⚠️ Non-Obvious Detail:** The `r3f.d.ts` file must declare every Three.js JSX element used (e.g., `mesh`, `torusGeometry`, `ambientLight`) — missing declarations cause TypeScript build failures.

### Module H: Orbital Dashboard Shell & Page Architecture
- **Files:** `App.tsx`, `components/layout/AppShell.tsx`, `index.css` (design tokens)
- **Responsibility:** Provides the authenticated layout (sidebar + top bar + content area) and React Router page routing for all features.
- **How it works:** After login, `App.tsx` renders `<AppShell>` wrapping `<Routes>`. `AppShell.tsx` provides a collapsible sidebar (Dashboard, Equipment, Maintenance, Reports, Admin), top bar with user name + role badge + theme toggle + sign out, and a content area that renders the active route's page component. All pages use shared design tokens defined in `index.css` — CSS variables (`--foreground`, `--background`, `--card`, `--primary`, `--border`, `--accent`, etc.) with separate `:root` (light) and `.dark` (dark) values. The `.glass-card` utility class uses `backdrop-blur` + themed borders for glassmorphism.
- **⚠️ Non-Obvious Detail:** The sidebar's Admin link is **not gated at all** (H1-12) — the frontend has no per-user capability signal to filter on (SEC-H10, deferred), so every nav item renders for every authenticated user and the backend's `MANAGE_PERSONNEL` check on the routes `/admin` calls is the real boundary; an unauthorized visitor gets a 403, not a hole. The current route is synced via React Router's `useLocation()` + `useNavigate()`, not component state.

**Frontend Route → Page Component Map:**

| Route | Component | Source File |
|-------|-----------|-------------|
| `/dashboard` | `DashboardPage` | `features/dashboard/components/DashboardPage.tsx` |
| `/equipment` | `EquipmentPage` | `features/equipment/components/EquipmentPage.tsx` |
| `/maintenance` | `MaintenancePage` | `features/maintenance/components/MaintenancePage.tsx` |
| `/reports` | `GeneralReportPage` | `features/reports/components/GeneralReportPage.tsx` |
| `/admin` | `AdminPanel` | `features/dashboard/components/AdminPanel.tsx` |

---

## 4. 🔌 Backend API Endpoints (Complete Reference)

### Auth (`routers/auth.py`)
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/login` | OAuth2 password login → JWT token |

### Users (`routers/users.py`)
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/users/` | Create user (`MANAGE_PERSONNEL`; requires `group_id`) |
| `PUT` | `/users/{user_id}/group` | Reassign a user's group (`MANAGE_PERSONNEL`) |
| `GET` | `/users/me` | Current user profile |
| `GET` | `/users/me/equipment` | Current user's held equipment |
| `GET` | `/users` | List all users (searchable, limit 50) |

### Equipment (`routers/equipment.py`)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/equipment` | **Matrix-filtered** equipment list |
| `POST` | `/equipment` | Add new equipment (by catalog name) |
| `PUT` | `/equipment/assign_owner` | Assign permanent owner |
| `POST` | `/equipment/transfer` | Transfer possession (person XOR location) |
| `POST` | `/equipment/{id}/verify` | Daily verification stamp |

### Maintenance (`routers/maintenance.py`)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/tickets/` | List tickets (optional status filter) |
| `POST` | `/maintenance/report` | Report fault → create ticket |
| `POST` | `/maintenance/fix/{id}` | Fix equipment → close tickets |

### Verifications (`routers/verifications.py`)
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/verifications/` | Submit detailed condition report |
| `GET` | `/verifications/equipment/{id}` | Verification history for equipment |
| `GET` | `/equipment/{id}/history` | Status change audit trail |

### Setup (`routers/setup.py`)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/groups` | List all groups (`MANAGE_PERSONNEL`) |
| `GET` | `/setup/fault_types` | List all fault types |
| `GET` | `/setup/fault_types/pending` | List pending fault types (manager only) |
| `POST` | `/setup/fault_types` | Create fault type |
| `PUT` | `/setup/fault_types/{id}/approve` | Approve pending fault type |
| `DELETE` | `/setup/fault_types/{id}` | Delete fault type |

### Reports (`routers/reports.py`)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/reports/query` | Inventory report (matrix-filtered + user filters) |
| `GET` | `/reports/daily_movement` | Last 24h transaction log |

### Analytics (`routers/analytics.py`)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/analytics/unit_readiness` | Total/functional/readiness % |

---

## 5. 🩸 The Memory & Data Flow (The "Blood")

### Input (Where data starts)
- **Frontend Forms** → React components → Axios → FastAPI endpoints
- **Seed Script** → `seed_data.py` builds the group graph, then bulk-inserts Users, Catalogs, Equipment. Requires `SEED_ENABLED=1` and a local `DATABASE_URL`; only destroys data with `--reset`
- **JWT Login** → `POST /login` → Token stored in `localStorage`

### Bootstrapping a Fresh System

There is deliberately **no HTTP path** that creates the first administrator. `POST /users/`
requires the `MANAGE_PERSONNEL` grant, and the old `POST /setup/initialize_system` was removed —
both were anonymous takeover routes.

To stand up an empty database, run once on the host:

```bash
BOOTSTRAP_ADMIN_ENABLED=1 python -m backend.bootstrap_admin <personal_number> "<full name>"
```

It applies migrations, creates the MASTER user, then places it at the top of the group graph
(creating a `ROOT` group if none exists) with a `GroupMembership` and every `Capability` granted
on each root — `bootstrap_admin.grant_root_authority()`, not a profile — and prints a generated
password **once**: it is not stored in plaintext and cannot be recovered. It refuses if root
authority is already held by anyone (`already_bootstrapped()` checks group membership on the
root, not a role column).

For a populated demo environment instead, use the seed (see hazard note 8).

### Seed Accounts (Created by `seed_data.py`)

> **Passwords are generated per account at seed time and printed once to stdout.**
> They are not stored in plaintext and are not recoverable — capture them from the
> seed output. Seeding requires `SEED_ENABLED=1`.

| Username | Full Name | Group (`GroupMembership`) | `VIEW` grant sits on | What they see |
|----------|-----------|---------------------------|-----------------------|----------------|
| `u_master` | Master Admin | `188` | `188` (root) | Everything under Brigade 188 |
| `u_brig_cmdr` | Brigade Commander | `188` | `188` (root) | Everything under Brigade 188 |
| `u_bn_cmdr` | Battalion Commander | `188/53` | `188/53` | Everything under Battalion 53 |
| `u_tech_bat` | Bat Tech Soldier | `188/53` | `188/53` | Everything under Battalion 53 |
| `u_co_cmdr_a` | Commander Co A | `188/53/A` | `188/53/A` | Company A only |
| `u_co_cmdr_b` | Commander Co B | `188/53/B` | `188/53/B` | Company B only |
| `u_soldier` | Simple Soldier | `188/53/A` | *(none)* | Own held equipment only (possession arm, not a grant) |

Group membership and `VIEW` placement happen to coincide for six of these seven — see the
`grants` dict comment in `seed_data.py` for why that is a seed-scale coincidence, not a rule.
`TRANSFER`, `CREATE_EQUIPMENT`, `REPORT_STATUS`, `RESOLVE_FAULT`, `MANAGE_CATALOG` and
`MANAGE_PERSONNEL` are each their own, narrower table — see Module F2.

### Storage (Where data lives)
| Table | Purpose |
|-------|---------| 
| `users` | Credentials, active-duty flag |
| `groups` | `Group` (polymorphic on `kind`) — nodes in the org DAG |
| `group_edges` | `GroupEdge` — direct containment |
| `group_closure` | `GroupClosure` — materialised transitive closure |
| `group_memberships` | `GroupMembership` — who sits where |
| `grants` | `Grant` — who may exercise which `Capability` over which group |
| `equipment` | The core entity: serial, status, owner, holder, location |
| `catalog_items` | Equipment type definitions (Radio 710, Ceramic Vest, etc.) |
| `locations` | Physical storage (Armory, Container, etc.) |
| `fault_types` | Known fault categories + pending approval flag |
| `transaction_logs` | Append-only log of every movement/handover/verification |
| `maintenance_logs` | Fault tickets (Open → In Progress → Closed) |
| `verifications` | Detailed condition reports |
| `equipment_status_history` | Audit: old_status → new_status with reason + verification link |
| `daily_stats` | Cached readiness snapshots (total, functional, score) |
| `solution_types` | Fix categories (Replace, Fix) |

### Output (Where data goes)
- **Login Page** → Orbital-style landing with 3D globe, theme toggle, inline login form
- **Dashboard** (`/dashboard`) → Welcome card, stats grid (4 stat cards with animated rings), equipment preview (top 5), activity feed (last 8 events)
- **Equipment** (`/equipment`) → Full equipment table with search/filter (by serial, type, status), expandable inline history rows, action modals (Report Fault with fault type picker + "other", Transfer with person/location toggle, Assign Owner with user search), Verification Form, full History modal
- **Maintenance** (`/maintenance`) → Ticket management: 4 summary stat cards, filter tabs (All/Open/In Progress/Closed), ticket cards with equipment name + fault type + dates, manager "close & fix" action
- **Reports** (`/reports`) → `GET /reports/query` with dynamic filters → table display, CSV export, print support
- **Admin** (`/admin`) → User search, group assignment
- **API Docs** → FastAPI auto-generated at `/docs`
- **All pages** → Full dark/light theme support via CSS variables (`.glass-card`, `text-foreground`, `bg-background`, etc.), Hebrew-first labels, RTL layout

---

## 6. 🐳 Docker & Infrastructure

### Docker Compose Services

| Service | Image / Build | Port | Purpose |
|---------|---------------|------|---------|
| `db` | `postgres:15-alpine` | `5432` | PostgreSQL database with persistent volume |
| `backend` | `Dockerfile.backend` (Python 3.10) | `8000` | FastAPI + uvicorn with hot-reload |
| `frontend` | `frontend/Dockerfile` (Node) | `3000` | React dev server |

### Database Connection
- **Docker:** `DATABASE_URL=postgresql://user:password@db:5432/military_db` (from env)
- **Local fallback:** `sqlite:///./sql_app.db` (when `DATABASE_URL` not set)
- **File:** `backend/database.py` — auto-detects SQLite vs PostgreSQL and adjusts `connect_args`

### Key Environment Variables
| Variable | Where | Description |
|----------|-------|-------------|
| `SECRET_KEY` | `.env` | JWT signing key (required, crashes if missing) |
| `DATABASE_URL` | `docker-compose.yml` | PostgreSQL connection string |
| `VITE_API_URL` | `docker-compose.yml` | Backend URL for frontend Axios |
| `SEED_ENABLED` | shell, per-run | Must be `1` for `seed_data.py` to run at all |
| `BOOTSTRAP_ADMIN_ENABLED` | shell, per-run | Must be `1` for `bootstrap_admin.py` to create the first MASTER |

---

## 7. 🚫 The "No-Fly Zone" (Critical Constraints)

> Things that broke before. **Do NOT repeat these mistakes.**

1. **DO NOT use `allow_origins=["*"]` in CORS.** It breaks cookie-based auth. Always list explicit origins (`localhost:3000`, `localhost:5173`).

2. ~~**DO NOT remove the `unit_hierarchy` field**~~ — **done, H1-11.** `users.unit_hierarchy`, `users.unit_path`, `equipment.unit_hierarchy` and `locations.unit_path` no longer exist, and `equipment.group_id` is now `NOT NULL`. Visibility runs on the group graph. **DO NOT re-add a path string:** `startswith()` compiled to an unescaped `LIKE`, so a value of `"%"` read the whole force — the encoding was the defect, which is why it was replaced rather than validated.

3. ~~**DO NOT change the order of the security filter cascade**~~ — **there is no cascade.** The MASTER → battalion → company → personal ladder was deleted at H1-10 along with `is_master`; `scope_equipment_query()` is now two OR'd arms with no precedence between them. **DO NOT reintroduce ordering:** an ordered filter where the first match wins is how a narrower rule silently stopped applying.

4. **DO NOT initialize React state that depends on API data with non-null defaults.** This caused a blank screen crash. Always use `null` initial state and guard with `if (loading)` checks.

5. **DO NOT import `React` in components** unless you actually use `React.something`. The JSX transform handles it. Unused imports break the strict TypeScript build.

6. **DO NOT use `import { Type }` for TypeScript types.** Always use `import type { Type }` — `verbatimModuleSyntax` is enabled and will fail the build otherwise.

7. ~~**The `Profile` model uses `BaseModel := Base` (walrus operator).**~~ — **moot, H1-12.** `Profile` itself is deleted; there is no walrus assignment left anywhere in `models.py`.

8. **`seed_data.py` requires `SEED_ENABLED=1` and a local `DATABASE_URL`, and only drops the schema with `--reset`.** Plain `python -m backend.seed_data` runs migrations and populates an empty database, refusing if user rows already exist. `--reset` first runs `DROP SCHEMA public CASCADE` then recreates — this **destroys all data**. The drop uses CASCADE rather than `drop_all()` because PostgreSQL has tables (`compliance_logs`, `inventory_audits`) with foreign keys not tracked by SQLAlchemy models, which `drop_all()` can't order correctly. The host in `DATABASE_URL` must be local (`localhost`, `127.0.0.1`, `::1`, or the compose service `db`); anything else is refused, because `SEED_ENABLED` travels in shell profiles and `.env` files while `DATABASE_URL` is what picks the victim.

9. **Schema changes go through Alembic — never `create_all()`.** `backend/migrations.py:run_migrations()` runs `alembic upgrade head` at startup, at seed time, and in the admin bootstrap. To change the schema: edit `backend/models.py`, then `alembic revision --autogenerate -m "what changed"`, review the generated file in `alembic/versions/`, and commit it. `alembic check` reports drift between the models and head. A pre-Alembic database with tables but no `alembic_version` is stamped automatically if it already matches the models, and refused with a diff if it is missing anything they declare — `create_all()` never added columns to existing tables, so older databases can be behind.

10. **`compliance_level` and `current_state_description` are computed properties,** not database columns. Don't try to query/filter by them directly in SQL.

11. **The `erasableSyntaxOnly` tsconfig option was removed** because the TypeScript version doesn't support it. Don't add it back.

12. **DO NOT define duplicate Pydantic classes in `schemas.py`.** Python uses the **last** definition. A duplicate `UnitReadinessResponse` with `readiness_score` silently overrode the correct one with `readiness_percentage`, causing a 500 crash. Always search for existing classes before adding new ones.

13. **Stale `__pycache__` on Windows can silently use old code.** Python caches `.pyc` files and Windows locks them while any Python process runs. If a code change isn't taking effect, run these 3 steps **in order:**
    1. `Get-Process python* | Stop-Process -Force`
    2. `Get-ChildItem -Recurse -Directory -Filter "__pycache__" -Path "c:\Users\asafs\Documents\Marker_System" | Where-Object { $_.FullName -notlike "*\venv\*" } | Remove-Item -Recurse -Force`
    3. `$env:PYTHONDONTWRITEBYTECODE="1"; .\venv\Scripts\python -m uvicorn backend.main:app --port 8000`

14. **When a CORS error shows `Status code: 500`, the real bug is on the backend.** FastAPI's CORS middleware only adds headers to successful responses. A 500 crash means no CORS headers → the browser reports "CORS Missing" instead of "500 Internal Server Error". Always check the backend terminal for the real traceback.

15. **The `analytics.py` endpoint returns a plain dict**, not a Pydantic `response_model`. This was done intentionally to avoid `__pycache__` staleness issues with the `UnitReadinessResponse` schema.

16. **Frontend expects `GET /setup/fault_types/pending`** — this endpoint must exist in `setup.py`. Without it, `DashboardPage.tsx` gets a 405 and fails to set `isManager`, breaking the manager UI.

17. **The `reports.py` endpoint returns a plain dict** matching the frontend `GeneralReportItem` interface (`item_type`, `unit_association`, `designated_owner`, `actual_location`, `serial_number`, `reporting_status`, `last_reporter`, `last_verified_at`). Equipment type = `item.catalog_item.name`, NOT `item.item_name`. Since H1-11, `unit_association` is the **group's name** (`item.group.name`) — the path columns it used to read are gone, and the eager load on `Equipment.group` is required or the report is an N+1.

18. **`tailwind.config.cjs` and `postcss.config.cjs` MUST use `.cjs` extension and CommonJS syntax** (`module.exports` + `require()`). The `package.json` has `"type": "module"` (ESM mode), which makes `.js` files ESM by default. But Tailwind v3's internal `jiti` loader doesn't support ESM features like top-level `await`, and `require()` is unavailable in ESM. Using `.cjs` forces CommonJS mode where `require()` works. Don't rename them back to `.js`.

19. **Stale Docker anonymous volumes can cause missing `node_modules` packages.** The `docker-compose.yml` uses `/app/node_modules` as an anonymous volume to preserve container deps. But this volume persists across rebuilds — if a new dependency (e.g., `tailwindcss-animate`) is added to `package.json`, the old volume won't have it. Fix: `docker-compose down` (removes anonymous volumes) then `docker-compose up --build`.

20. **The 3D globe requires `three`, `@react-three/fiber`, `@react-three/drei`, and `@types/three`.** These are the rendering stack for `NetworkGlobe.tsx`. The file `src/r3f.d.ts` provides TypeScript JSX intrinsic element declarations (`mesh`, `group`, `torusGeometry`, etc.) for React Three Fiber — if you add a new Three.js element to the globe, you must also declare it in `r3f.d.ts`. Don't remove these packages or the declaration file.

---

## 8. 📋 Versioning & Release History

This project uses **Semantic Versioning** (`MAJOR.MINOR.PATCH`):
- **MAJOR:** Breaking changes (DB schema redesign, auth overhaul)
- **MINOR:** New features (new page, new endpoint), backward compatible
- **PATCH:** Bug fixes, typo corrections

> Pre-production versions use `0.x.y`. First production release will be `1.0.0`.

| Version | Date | Summary |
|---------|------|---------|
| `0.1.0` | — | Initial system: equipment CRUD, basic auth, dashboard |
| `0.2.0` | — | Maintenance tickets, reports, daily verification, Matrix Security |
| `0.3.0` | 2026-02-15 | Modular architecture (routers), Orbital login, AppShell, feature-based frontend, Docker compose, Verification & Status History |
| `0.5.0` | 2026-08-28 | **Access-control release.** Group DAG + capability grants replace the materialized-path hierarchy and the `Profile` matrix; all four Critical audit findings closed; Alembic adopted; first test suite (306) and CI restored. See [CHANGELOG.md](CHANGELOG.md) |
