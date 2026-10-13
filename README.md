# Military Logistics ("Marker System")

Tracks military equipment ownership, location and operational readiness across a hierarchy of units: brigade, battalion, company, soldier.

The project is pre-production (`0.x`). What this repository starts is a development stack on one machine. `docker-compose.yml` is not a deployment: it serves plain http, mounts the source from the host and reloads on a change.

## Tech stack

- **Frontend:** React, TypeScript, Vite, Tailwind CSS
- **Backend:** FastAPI on Python 3.10
- **Database:** PostgreSQL
- **Local stack:** Docker Compose

## Quick start

Needs Docker with Compose, the `docker compose` command. From the top of the repository:

- Make the environment file: `cp .env.example .env`. Then give `SECRET_KEY`, `POSTGRES_USER` and `POSTGRES_PASSWORD` a value in it. The stack does not start without the three. The comments in the file say how to make the key and which characters the password may have.
- Build and start the three services: `docker compose up --build`
- Make the first accounts, in a second terminal: `docker compose exec -e SEED_ENABLED=1 backend python -m backend.seed_data`

The stack is up when the frontend answers:

| What | Where |
|---|---|
| The application | `http://localhost:3000` |
| The API | `http://localhost:8000` |
| The API's documentation | `http://localhost:8000/docs` |
| The API's health check | `http://localhost:8000/health` |

The server migrates the database when it starts, so there is no migration to run.

A new database has no user, and nobody can log in until the seed has run. The seed makes seven accounts, from `u_master` down to `u_soldier`, with units and equipment for them. It prints each account's password once and they cannot be read again, so copy them from the terminal. Log in on the application's page with an account's name as the personal number. The seed refuses a database that already has a user.

To start with one administrator and none of the seed's accounts, units and equipment, run [backend/bootstrap_admin.py](backend/bootstrap_admin.py) in place of the seed, the same way: `docker compose exec -e BOOTSTRAP_ADMIN_ENABLED=1 backend python -m backend.bootstrap_admin <personal number> "<full name>"`. It makes the account, a unit named ROOT for it to command when the database has no group, and prints the password once. After it the seed refuses, since the database has a user.

## Tests and local development

[CONTRIBUTING.md](CONTRIBUTING.md#testing) gives the commands for the backend's and the frontend's tests, what to install for them, and the branch and pull request steps. [frontend/README.md](frontend/README.md) is about working in the frontend's folder.

## Project structure

```
├── backend/             # The FastAPI application
├── alembic/             # Database migrations
├── tests/               # The pytest suite: the backend, the images, the workflow, the documents
├── frontend/            # The React application and its tests
├── .github/workflows/   # The CI workflow
├── docker-compose.yml   # The development stack
├── Dockerfile.backend   # The backend's image
└── .env.example         # The environment file to copy
```

## Documents

- [SYSTEM_MAP.md](SYSTEM_MAP.md): the architecture, the data model, the endpoints and the rules not to break.
- [CONTRIBUTING.md](CONTRIBUTING.md): the workflow, the tests and what keeps the map true.
- [CHANGELOG.md](CHANGELOG.md): what each version changed.
- [TODO.md](TODO.md): the audit's findings, open and closed.

## License

Copyright (c) 2026 AsafCodes. All Rights Reserved.

The source is public to be read. No permission to use, copy or distribute it is given. See [LICENSE](LICENSE).
