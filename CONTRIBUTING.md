# Contributing Guidelines

## Development Workflow

1.  **Branching**:
    - Use the format: `type/description`
    - Types: `feat`, `fix`, `infra`, `docs`, `refactor`
    - Example: `feat/add-login-page`, `fix/cors-issue`

2.  **Commits**:
    - Use clear, descriptive commit messages.
    - Start with a capitalized verb (e.g., "Add...", "Fix...", "Update...").

3.  **Pull Requests**:
    - Rebase on `main` before opening.
    - Describe changes clearly.
    - Ensure all tests pass.

## Code Style

- **Python**: Follow PEP 8. We use `ruff` for linting and formatting.
- **TypeScript**: Follow standard React/TS patterns.

## Testing

- Run backend tests: `pytest`
- Run frontend linting: `npm run lint`
