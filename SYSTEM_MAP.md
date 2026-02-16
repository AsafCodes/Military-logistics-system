# 🗺️ SYSTEM MAP — Military Logistics ("Marker System")

> **Version:** 0.3.0
> **Last Updated:** 2026-02-15
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
│   ├── models.py               # All ORM models (13 tables)
│   ├── schemas.py              # All Pydantic request/response schemas
│   ├── security.py             # JWT + password hashing + Matrix Security filter
│   ├── dependencies.py         # Auth dependencies + compliance helper
│   ├── seed_data.py            # Bulk-insert test data (⚠️ destructive)
│   └── routers/                # Modular API endpoints
│       ├── auth.py             # POST /login
│       ├── users.py            # CRUD + /users/me + /users/promote
│       ├── equipment.py        # Equipment CRUD + transfer + daily verify
│       ├── maintenance.py      # Fault reporting + ticket management + fix
│       ├── verifications.py    # Detailed condition verification + status history
│       ├── setup.py            # System init + fault type CRUD + profiles
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
│       │   │   ├── SearchableMultiSelect.tsx
│       │   │   └── ParticlesBackground.tsx
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
│       │   │   │   ├── ExportControls.tsx   # PDF/Excel export buttons
│       │   │   │   └── AdminPanel.tsx       # User search, role/profile mgmt
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
├── requirements.txt            # Python dependencies
├── .env / .env.example         # SECRET_KEY configuration
└── SYSTEM_MAP.md               # ← You are here
```

---

## 3. 🧠 Core Logic Modules (The "Brains")

### Module A: Matrix Security Engine
- **Files:** `security.py` → `get_visible_equipment()`, `equipment.py` → `get_accessible_equipment()`
- **Responsibility:** Decides **who sees what** based on `unit_hierarchy` path matching.
- **How it works:** A user with `unit_hierarchy = "188/53"` sees all equipment under `188/53/*`. A soldier only sees their own items.
- **⚠️ Non-Obvious Detail:** The filter cascades: MASTER → `can_view_all` → `can_view_battalion` → `can_view_company` → personal only. Order matters — it goes broadest to narrowest and the first match wins.

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
- **⚠️ Non-Obvious Detail:** Non-managers who create a new fault type get `is_pending=True` — it needs manager approval before it shows up in the general list.

### Module E: Verification & Audit Trail
- **Files:** `routers/verifications.py` (2 sub-routers: `router` + `history_router`)
- **Responsibility:** Records detailed equipment condition reports. If the reported status differs from current status, automatically creates an `EquipmentStatusHistory` entry linked to the verification.
- **Endpoints:** `POST /verifications/` (create), `GET /verifications/equipment/{id}` (list), `GET /equipment/{id}/history` (status changes).

### Module F: Profile Permission Matrix ("The Green Table")
- **Files:** `models.py` → `Profile` (20+ boolean flags), `seed_data.py`
- **Responsibility:** Controls what each role can do (view, transfer, fix, report, etc.). Seeded with predefined profiles (Master → Soldier).
- **Key Flags:** `can_view_all_equipment`, `can_view_battalion_realtime`, `can_view_company_realtime`, `can_change_maintenance_status`, `can_change_assignment_others`, `can_add_category`, `can_remove_category`, `can_assign_roles`, `holds_equipment`, `must_report_presence`.

### Module G: Login Page & 3D Globe ("Orbital" Design)
- **Files:** `features/auth/components/LoginPage.tsx`, `components/ui/NetworkGlobe.tsx`, `components/ui/ThemeToggle.tsx`, `r3f.d.ts`
- **Responsibility:** Full-screen login page with an Orbital-style layout: hero text + inline login form (left 45%), animated 3D particle globe (right 55%), stats bar (bottom), navbar with dark/light theme toggle.
- **How it works:** `NetworkGlobe.tsx` uses React Three Fiber (`@react-three/fiber`) + drei helpers (`Points`, `PointMaterial`) to render 3,000 uniformly-distributed particles on a sphere. A `<torus>` ring orbits the sphere. Colors, particle size, ring opacity, and glow opacity are all **theme-aware**. `ThemeToggle.tsx` toggles `.dark` class on `<html>`, persists to `localStorage`, and `LoginPage.tsx` watches for class changes via `MutationObserver` to pass `isDark` to the globe.
- **⚠️ Non-Obvious Detail:** The `r3f.d.ts` file must declare every Three.js JSX element used (e.g., `mesh`, `torusGeometry`, `ambientLight`) — missing declarations cause TypeScript build failures.

### Module H: Orbital Dashboard Shell & Page Architecture
- **Files:** `App.tsx`, `components/layout/AppShell.tsx`, `index.css` (design tokens)
- **Responsibility:** Provides the authenticated layout (sidebar + top bar + content area) and React Router page routing for all features.
- **How it works:** After login, `App.tsx` renders `<AppShell>` wrapping `<Routes>`. `AppShell.tsx` provides a collapsible sidebar (Dashboard, Equipment, Maintenance, Reports, Admin), top bar with user name + role badge + theme toggle + sign out, and a content area that renders the active route's page component. All pages use shared design tokens defined in `index.css` — CSS variables (`--foreground`, `--background`, `--card`, `--primary`, `--border`, `--accent`, etc.) with separate `:root` (light) and `.dark` (dark) values. The `.glass-card` utility class uses `backdrop-blur` + themed borders for glassmorphism.
- **⚠️ Non-Obvious Detail:** The sidebar's Admin link is conditional on `user.role === 'master'`. The current route is synced via React Router's `useLocation()` + `useNavigate()`, not component state.

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
| `POST` | `/users/` | Create user (first user = master) |
| `PUT` | `/users/promote` | Promote user role (MASTER only) |
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
| `POST` | `/setup/initialize_system` | Create default profiles (run once) |
| `GET` | `/profiles` | List all profiles |
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
- **Seed Script** → `seed_data.py` bulk-inserts Profiles, Users, Catalogs, Equipment (⚠️ destroys all data first!)
- **JWT Login** → `POST /login` → Token stored in `localStorage`

### Seed Accounts (Created by `seed_data.py`)

> **All accounts use password: `secret`**

| Username | Full Name | Role | Profile | Hierarchy | What they see |
|----------|-----------|------|---------|-----------|---------------|
| `u_master` | Master Admin | master | Master | *(all)* | Everything |
| `u_brig_cmdr` | Brigade Commander | manager | Brigade Tech Commander | `188` | All under Brigade 188 |
| `u_bn_cmdr` | Battalion Commander | manager | Battalion Tech Commander | `188/53` | All under Battalion 53 |
| `u_tech_bat` | Bat Tech Soldier | technician | Battalion Tech Soldier | `188/53` | All under Battalion 53 |
| `u_co_cmdr_a` | Commander Co A | manager | Company Commander | `188/53/A` | Company A only |
| `u_co_cmdr_b` | Commander Co B | manager | Company Commander | `188/53/B` | Company B only |
| `u_soldier` | Simple Soldier | user | Soldier | `188/53/A` | Own equipment only |

### Storage (Where data lives)
| Table | Purpose |
|-------|---------| 
| `users` | Credentials, role, hierarchy path, profile link |
| `profiles` | The "Green Table" — 20+ permission booleans |
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
- **Admin** (`/admin`) → User search, role promotion, profile assignment
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

---

## 7. 🚫 The "No-Fly Zone" (Critical Constraints)

> Things that broke before. **Do NOT repeat these mistakes.**

1. **DO NOT use `allow_origins=["*"]` in CORS.** It breaks cookie-based auth. Always list explicit origins (`localhost:3000`, `localhost:5173`).

2. **DO NOT remove the `unit_hierarchy` field or change its format.** The entire Matrix Security filter depends on slash-separated paths like `"188/53/A"`. Changing this breaks all visibility logic.

3. **DO NOT change the order of the security filter cascade** in `equipment.py` (MASTER → battalion → company → personal). It's intentionally ordered from broadest to narrowest.

4. **DO NOT initialize React state that depends on API data with non-null defaults.** This caused a blank screen crash. Always use `null` initial state and guard with `if (loading)` checks.

5. **DO NOT import `React` in components** unless you actually use `React.something`. The JSX transform handles it. Unused imports break the strict TypeScript build.

6. **DO NOT use `import { Type }` for TypeScript types.** Always use `import type { Type }` — `verbatimModuleSyntax` is enabled and will fail the build otherwise.

7. **The `Profile` model uses `BaseModel := Base` (walrus operator).** This is intentional. Don't "fix" it.

8. **`seed_data.py` uses `DROP SCHEMA public CASCADE` then `create_all()`.** This was changed from `drop_all()` because PostgreSQL has tables (`compliance_logs`, `inventory_audits`) with foreign keys not tracked by SQLAlchemy models — `drop_all()` can't resolve the drop order and crashes. Running seed **destroys all data**. Never run in production.

9. **`compliance_level` and `current_state_description` are computed properties,** not database columns. Don't try to query/filter by them directly in SQL.

10. **The `erasableSyntaxOnly` tsconfig option was removed** because the TypeScript version doesn't support it. Don't add it back.

11. **DO NOT define duplicate Pydantic classes in `schemas.py`.** Python uses the **last** definition. A duplicate `UnitReadinessResponse` with `readiness_score` silently overrode the correct one with `readiness_percentage`, causing a 500 crash. Always search for existing classes before adding new ones.

12. **Stale `__pycache__` on Windows can silently use old code.** Python caches `.pyc` files and Windows locks them while any Python process runs. If a code change isn't taking effect, run these 3 steps **in order:**
    1. `Get-Process python* | Stop-Process -Force`
    2. `Get-ChildItem -Recurse -Directory -Filter "__pycache__" -Path "c:\Users\asafs\Documents\Marker_System" | Where-Object { $_.FullName -notlike "*\venv\*" } | Remove-Item -Recurse -Force`
    3. `$env:PYTHONDONTWRITEBYTECODE="1"; .\venv\Scripts\python -m uvicorn backend.main:app --port 8000`

13. **When a CORS error shows `Status code: 500`, the real bug is on the backend.** FastAPI's CORS middleware only adds headers to successful responses. A 500 crash means no CORS headers → the browser reports "CORS Missing" instead of "500 Internal Server Error". Always check the backend terminal for the real traceback.

14. **The `analytics.py` endpoint returns a plain dict**, not a Pydantic `response_model`. This was done intentionally to avoid `__pycache__` staleness issues with the `UnitReadinessResponse` schema.

15. **Frontend expects `GET /setup/fault_types/pending`** — this endpoint must exist in `setup.py`. Without it, `DashboardPage.tsx` gets a 405 and fails to set `isManager`, breaking the manager UI.

16. **The `reports.py` endpoint returns a plain dict** matching the frontend `GeneralReportItem` interface (`item_type`, `unit_association`, `designated_owner`, `actual_location`, `serial_number`, `reporting_status`, `last_reporter`, `last_verified_at`). Equipment type = `item.catalog_item.name`, NOT `item.item_name`. User model has `unit_hierarchy`, NOT `unit_path`.

17. **`tailwind.config.cjs` and `postcss.config.cjs` MUST use `.cjs` extension and CommonJS syntax** (`module.exports` + `require()`). The `package.json` has `"type": "module"` (ESM mode), which makes `.js` files ESM by default. But Tailwind v3's internal `jiti` loader doesn't support ESM features like top-level `await`, and `require()` is unavailable in ESM. Using `.cjs` forces CommonJS mode where `require()` works. Don't rename them back to `.js`.

18. **Stale Docker anonymous volumes can cause missing `node_modules` packages.** The `docker-compose.yml` uses `/app/node_modules` as an anonymous volume to preserve container deps. But this volume persists across rebuilds — if a new dependency (e.g., `tailwindcss-animate`) is added to `package.json`, the old volume won't have it. Fix: `docker-compose down` (removes anonymous volumes) then `docker-compose up --build`.

19. **The 3D globe requires `three`, `@react-three/fiber`, `@react-three/drei`, and `@types/three`.** These are the rendering stack for `NetworkGlobe.tsx`. The file `src/r3f.d.ts` provides TypeScript JSX intrinsic element declarations (`mesh`, `group`, `torusGeometry`, etc.) for React Three Fiber — if you add a new Three.js element to the globe, you must also declare it in `r3f.d.ts`. Don't remove these packages or the declaration file.

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
