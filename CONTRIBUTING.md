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

## Testing

- Run backend tests: `pytest`
- Run frontend linting: `npm run lint`
