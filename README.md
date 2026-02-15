# Marker System

> **CONFIDENTIAL**: This proprietary software is for authorized use only.

## Overview
Military Logistics "Marker System" tracks equipment ownership, location, and operational readiness across a hierarchical unit structure. It provides real-time visibility into equipment status, maintenance, and compliance.

## Tech Stack
- **Frontend**: React, TypeScript, TailwindCSS, Vite
- **Backend**: FastAPI, Python 3.10+
- **Database**: PostgreSQL
- **Infrastructure**: Docker, Docker Compose

## Prerequisites
- Docker & Docker Compose
- Node.js 18+ (for local frontend dev)
- Python 3.10+ (for local backend dev)

## Getting Started

### Quick Start (Docker)
1.  **Clone the repository** (recurse submodules if any)
2.  **Setup Environment**:
    \`\`\`bash
    cp .env.example .env
    # Update .env with secure credentials
    \`\`\`
3.  **Run with Docker Compose**:
    \`\`\`bash
    docker-compose up --build
    \`\`\`
4.  Access the app at `http://localhost:5173` (Frontend) and `http://localhost:8000/docs` (Backend API).

### Local Development

#### Backend
\`\`\`bash
cd backend
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r ../requirements.txt
uvicorn main:app --reload
\`\`\`

#### Frontend
\`\`\`bash
cd frontend
npm install
npm run dev
\`\`\`

## Project Structure
\`\`\`
├── backend/            # FastAPI application
├── frontend/           # React application
├── tests/              # End-to-end and integration tests
├── .github/            # CI/CD workflows
├── docker-compose.yml  # Orchestration
└── SYSTEM_MAP.md       # Detailed Architecture Documentation
\`\`\`

## License
Copyright (c) 2026 AsafCodes. All Rights Reserved.
See [LICENSE](./LICENSE) for details.
