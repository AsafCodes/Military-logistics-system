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
4. Wait for the `CI` run on the pull request to pass (a green checkmark ✅). GitHub does not hold the merge for it: `main` has no required checks.
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
- A commit that adds or removes a route, or changes its method or its path, changes the tables in section 4 of the map in the same commit. `tests/test_system_map_endpoints.py` compares the method and path of every row there with the application's OpenAPI specification. It does not check what a description says. It also fails on `include_in_schema=` in a call under `backend/`, since a route kept out of the specification would be asked no row.
- `tests/test_system_map_refs.py` does not check the map's prose or the tree in its section 2. A new file, a file moved between the repository top, `backend/`, `backend/routers/` and `frontend/src/` that the map writes without its folder, or a function that keeps its name and changes what it does, needs the map read by hand.

## Testing

Backend, from the top of the repository, on Python 3.10:

- Install both locks: `pip install --require-hashes -r requirements.txt -r requirements-dev.txt`
- Run the tests: `python -m pytest`. A bare `pytest` stops with `No module named 'backend'`, since it does not put the directory it is run from on Python's path; `python -m pytest` does.

Frontend, in `frontend/`, after `npm ci`:

- Run the linter: `npm run lint`
- Run the typecheck: `npm run typecheck`
- Run the tests: `npm run test`
- Audit the runtime dependencies: `npm run audit:prod`

The `CI` workflow, `.github/workflows/ci.yml`, runs each of these commands on a pull request to `main`. `tests/test_contributing_commands.py` compares the six commands in the lists above, and the Python version, with the workflow and with `frontend/package.json`. It does not check the `npm ci`, the directories, or that a command passes.
