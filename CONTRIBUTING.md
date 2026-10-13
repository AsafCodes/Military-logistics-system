# Contributing Guidelines

## Development Workflow

## 🚀 Daily Workflow (The "Google Style" Cycle)

Follow this exact sequence for **every** new task (feature or bug fix).

### 1. sync (Start Clean)
Always start from the latest version of the main project.
```bash
git checkout main
git pull origin main
```

### 2. Branch (Create Sandbox)
Create a new branch for your specific task.
```bash
# Format: type/short-description
# Types: feat (new feature), fix (bug fix), docs (documentation), infra (config/devops)

git checkout -b feat/login-page
```

### 3. Work (The Loop)
1. Write code.
2. Test locally.
3. Save changes:
   ```bash
   git add .
   git commit -m "feat: Add login form layout"
   ```
4. Repeat specific commits as you make progress.

### 4. Push (Upload)
Send your branch to GitHub.
```bash
git push -u origin feat/login-page
```

### 5. Review (Pull Request)
1. Go to your GitHub repository URL.
2. Click **"Compare & pull request"**.
3. Create the PR.
4. Wait for the green checkmark ✅ (CI/CD passed).
5. Click **"Squash and merge"** or **"Merge pull request"**.

### 6. Cleanup
Delete the branch locally to keep your workspace clean.
```bash
git checkout main
git pull origin main
git branch -d feat/login-page
```

## Code Style

- **Python**: Follow PEP 8. We use `ruff` for linting and formatting.
- **TypeScript**: Follow standard React/TS patterns.

## Documentation

- A commit that moves, renames or deletes a file or a function that `SYSTEM_MAP.md` names changes the map in the same commit.
- `tests/test_system_map_refs.py` checks the names on the map's `Files:` lines. Each `.py`, `.ts`, `.tsx` or `.css` file named there must be found in one place, and each `file.py` → `name` must be a `def`, a `class` or an assignment in that file.
- It does not check the map's prose or the tree in its section 2. A new file, a file moved between the repository top, `backend/`, `backend/routers/` and `frontend/src/` that the map writes without its folder, or a function that keeps its name and changes what it does, needs the map read by hand.

## Testing

- Run backend tests: `pytest`
- Run frontend linting: `npm run lint`
- Run the frontend typecheck: `npm run typecheck`
- Run frontend tests: `npm run test`
- Audit the frontend's runtime dependencies: `npm run audit:prod`
