# 🗺️ SYSTEM MAP — Military Logistics ("Marker System")

> **Last Updated:** 2026-02-13
> **Solo Dev Reference.** Read this before touching anything.

---

## 1. 🌟 The "North Star" (The Goal)

**This software tracks military equipment ownership, location, and operational readiness across a hierarchical unit structure (Brigade → Battalion → Company → Soldier).**

It answers three questions at any moment:
1. **Where is every piece of equipment?** (Who holds it, where is it stored)
2. **Is it functional?** (Maintenance status, fault tickets)
3. **Is it accounted for?** (Daily verification compliance)

---

## 2. 🧠 Core Logic Modules (The "Brains")

### Module A: Matrix Security Engine
- **Files:** `security.py` → `get_visible_equipment()`, `equipment.py` → line 27-56
- **Responsibility:** Decides **who sees what** based on `unit_hierarchy` path matching.
- **How it works:** A user with `unit_hierarchy = "188/53"` sees all equipment under `188/53/*`. A soldier only sees their own items.
- **⚠️ Non-Obvious Detail:** The filter cascades: MASTER → `can_view_all` → `can_view_battalion` → `can_view_company` → personal only. Order matters — it goes broadest to narrowest and the first match wins.

### Module B: Compliance Engine
- **Files:** `models.py` → `Equipment.compliance_level`, `dependencies.py` → `get_daily_status()`
- **Responsibility:** Flags equipment as "GOOD" / "WARNING" / "SEVERE" based on time since last verification.
- **Rules:** <24h = GOOD, 24-48h = WARNING, >48h = SEVERE.
- **⚠️ Non-Obvious Detail:** `compliance_level` is a **computed property**, not stored in the DB. It recalculates on every read using `datetime.utcnow()`. You cannot query or filter by it in SQL.

### Module C: Ownership vs. Possession Model
- **Files:** `models.py` → `Equipment` (lines 76-83)
- **Responsibility:** Separates WHO OWNS an item (`owner_user_id`) from WHO HAS IT RIGHT NOW (`holder_user_id`) from WHERE IT IS (`custom_location` / `actual_location_id`).
- **⚠️ Non-Obvious Detail:** Transfer to a person clears location. Transfer to a location clears holder. These are mutually exclusive (XOR validation in `equipment.py`). Don't try to set both at once.

### Module D: Fault & Ticket Pipeline
- **Files:** `maintenance.py`
- **Responsibility:** `report_fault` → creates `FaultType` (if new) → creates `MaintenanceLog` ticket → marks equipment "Malfunctioning". `fix_equipment` → sets "Functional" → closes all open tickets.
- **⚠️ Non-Obvious Detail:** Non-managers who create a new fault type get `is_pending=True` — it needs manager approval before it shows up in the general list.

### Module E: Verification & Audit Trail
- **Files:** `verifications.py`
- **Responsibility:** Records detailed equipment condition reports. If the reported status differs from current status, automatically creates a `EquipmentStatusHistory` entry linked to the verification.

### Module F: Profile Permission Matrix ("The Green Table")
- **Files:** `models.py` → `Profile`, `seed_data.py`
- **Responsibility:** 20+ boolean flags that control what each role can do (view, transfer, fix, report, etc.). Seeded with 8 predefined profiles (Master → Soldier).
- **⚠️ Non-Obvious Detail:** The `Profile` class uses `BaseModel := Base` walrus operator assignment (line 203 of `models.py`). This is intentional — don't refactor it.

---

## 3. 🩸 The Memory & Data Flow (The "Blood")

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
- **Dashboard UI** → Equipment table, stats grid, compliance badges
- **Reports** → `GET /reports/query` with dynamic filters → CSV export
- **Admin Panel** → User management, role promotion, profile assignment
- **API Docs** → FastAPI auto-generated at `/docs`

---

## 4. 🚫 The "No-Fly Zone" (Critical Constraints)

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

12. **Stale `__pycache__` on Windows can silently use old code.** Python caches `.pyc` files and Windows locks them while any Python process runs. If a code change isn't taking effect, run these 3 steps **in order**:
    1. `Get-Process python* | Stop-Process -Force`
    2. `Get-ChildItem -Recurse -Directory -Filter "__pycache__" -Path "c:\Users\asafs\Documents\Marker_System" | Where-Object { $_.FullName -notlike "*\venv\*" } | Remove-Item -Recurse -Force`
    3. `$env:PYTHONDONTWRITEBYTECODE="1"; .\venv\Scripts\python -m uvicorn backend.main:app --port 8000`

13. **When a CORS error shows `Status code: 500`, the real bug is on the backend.** FastAPI's CORS middleware only adds headers to successful responses. A 500 crash means no CORS headers → the browser reports "CORS Missing" instead of "500 Internal Server Error". Always check the backend terminal for the real traceback.

14. **The `analytics.py` endpoint returns a plain dict**, not a Pydantic `response_model`. This was done intentionally to avoid `__pycache__` staleness issues with the `UnitReadinessResponse` schema.

15. **Frontend expects `GET /setup/fault_types/pending`** — this endpoint must exist in `setup.py`. Without it, `DashboardPage.tsx` line 144 gets a 405 and fails to set `isManager`, breaking the manager UI.

16. **The `reports.py` endpoint returns a plain dict** matching the frontend `GeneralReportItem` interface (`item_type`, `unit_association`, `designated_owner`, `actual_location`, `serial_number`, `reporting_status`, `last_reporter`, `last_verified_at`). Equipment type = `item.catalog_item.name`, NOT `item.item_name`. User model has `unit_hierarchy`, NOT `unit_path`.

17. **`tailwind.config.cjs` and `postcss.config.cjs` MUST use `.cjs` extension and CommonJS syntax** (`module.exports` + `require()`). The `package.json` has `"type": "module"` (ESM mode), which makes `.js` files ESM by default. But Tailwind v3's internal `jiti` loader doesn't support ESM features like top-level `await`, and `require()` is unavailable in ESM. Using `.cjs` forces CommonJS mode where `require()` works. Don't rename them back to `.js`.

18. **Stale Docker anonymous volumes can cause missing `node_modules` packages.** The `docker-compose.yml` uses `/app/node_modules` as an anonymous volume to preserve container deps. But this volume persists across rebuilds — if a new dependency (e.g., `tailwindcss-animate`) is added to `package.json`, the old volume won't have it. Fix: `docker-compose down` (removes anonymous volumes) then `docker-compose up --build`.
