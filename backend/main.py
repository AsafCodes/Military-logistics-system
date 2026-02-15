"""
Military Logistics System - FastAPI Entry Point
Modular Architecture v4.1
"""

import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import OperationalError

from . import models

# Internal Modules - relative imports within backend package
from .database import engine

# Routers
from .routers import (
    analytics,
    auth,
    equipment,
    maintenance,
    reports,
    setup,
    users,
    verifications,
)


# --- Database Initialization ---
def wait_for_db():
    max_retries = 30
    retry_interval = 2
    for i in range(max_retries):
        try:
            with engine.connect():
                pass
            print("Database connection established!")
            return
        except OperationalError:
            print(
                f"Database not ready... retrying in {retry_interval}s ({i + 1}/{max_retries})"
            )
            time.sleep(retry_interval)
    raise Exception("Database connection failed after multiple retries")


wait_for_db()
models.Base.metadata.create_all(bind=engine)

# --- FastAPI App ---
app = FastAPI(title="Military Logistics System", version="4.1 - Modular")

# --- CORS Middleware (Strict Origins) ---
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


print(
    "✅ SYSTEM READY: Backend is running on port 8000 and accepting connections from Port 3000"
)
