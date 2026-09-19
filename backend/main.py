"""
Military Logistics System - FastAPI Entry Point
Modular Architecture v4.1
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import OperationalError
import time

# Internal Modules - relative imports within backend package
from .database import engine
from .migrations import run_migrations

# Routers
from .routers import auth, users, equipment, maintenance, setup, reports, analytics, verifications

# --- Database Initialization ---
def wait_for_db():
    max_retries = 30
    retry_interval = 2
    for i in range(max_retries):
        try:
            with engine.connect() as conn:
                pass
            print("Database connection established!")
            return
        except OperationalError:
            print(f"Database not ready... retrying in {retry_interval}s ({i+1}/{max_retries})")
            time.sleep(retry_interval)
    raise Exception("Database connection failed after multiple retries")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Connect and migrate when the server starts, not when the module is imported.

    Both calls used to run at module scope, so `import backend.main` -- from a
    test, a script, or a tool reading the app object -- opened a connection,
    blocked up to 60s waiting for it, ran Alembic against whatever database
    DATABASE_URL happened to name, and raised on failure with no server around
    to recover. Uvicorn runs this handler after the process is up, so a failure
    here is a startup failure rather than an import failure.

    wait_for_db stays synchronous on purpose: uvicorn serves nothing until
    lifespan startup returns, so blocking the loop here is the intended
    semantics, not an oversight.

    NOT a fix for the multi-worker race: lifespan runs once per worker process,
    so N workers still run N migrations concurrently. Nothing in this repo runs
    more than one worker today; a deployment that does needs the migration
    hoisted out of the server entirely.
    """
    wait_for_db()
    run_migrations()
    print("✅ SYSTEM READY: Backend is running on port 8000 and accepting connections from Port 3000")
    yield


# --- FastAPI App ---
app = FastAPI(title="Military Logistics System", version="0.5.0", lifespan=lifespan)

# --- CORS Middleware (Strict Origins) ---
# SEC-H9 CAVEAT, and it costs an hour if you meet it without warning: since the
# session became a SameSite=Lax cookie, CORS approval is no longer sufficient to
# make an origin usable. `localhost` and `127.0.0.1` are different SITES to a
# browser, so a page served from http://127.0.0.1:3000 talking to an API on
# http://localhost:8000 is refused the cookie -- login returns 200, no session
# is established, and nothing in the console says why.
#
# The 127.0.0.1 entries are kept because they are legitimate for the
# Authorization-header clients (Swagger, curl, the test suite), which are
# unaffected. For a BROWSER, match the host on both ends: browse to
# http://localhost:3000 against http://localhost:8000, or set VITE_API_URL to
# the 127.0.0.1 form if you prefer that host.
origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins, 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Include Routers ---
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(equipment.router)
app.include_router(maintenance.router)
app.include_router(setup.router)
app.include_router(reports.router)
app.include_router(analytics.router)
app.include_router(verifications.router)
app.include_router(verifications.history_router)

# --- Root Endpoint ---
@app.get("/")
def read_root():
    return {"message": "Military Logistics System V4.1 (Modular) 🛡️"}
