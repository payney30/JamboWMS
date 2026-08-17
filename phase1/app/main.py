import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from .routers import work_orders, dashboard, reference, public, users, admin, task_workers, inventory
from .routers.public import UPLOAD_DIR
from .database import DATABASE_URL
from .auth import SECRET_KEY, DEV_SECRET_KEY

# Schema is managed by Alembic now (see /alembic and README) — run
# `alembic upgrade head` before starting the app. main.py no longer calls
# Base.metadata.create_all(); that call can't apply schema changes to an
# existing database and silently no-ops once a table exists, which is
# exactly the failure mode migrations exist to prevent.

# Refuse to boot against a real database with the fallback dev JWT secret
# still in place — that secret is right here in the source, so anyone who
# has read this file (or the public repo) could forge a valid login token.
# It's fine for local SQLite dev, where there's nothing at stake; it is
# not fine the moment DATABASE_URL points at a real Postgres instance.
# Set JWT_SECRET to a real random value (e.g. `openssl rand -hex 32`) as
# an environment variable on whatever platform this is deployed to.
if SECRET_KEY == DEV_SECRET_KEY and not DATABASE_URL.startswith("sqlite"):
    raise RuntimeError(
        "Refusing to start: JWT_SECRET is not set (or is still the dev "
        "default) while DATABASE_URL points at a non-SQLite database. "
        "Set JWT_SECRET to a real random value before deploying — e.g. "
        "`openssl rand -hex 32` — and set it as an environment variable "
        "on your hosting platform, not in code."
    )

app = FastAPI(title="NJ LOC Work Order System — Phase 1")

# The triage UI is served by this same app (see the StaticFiles mount
# below), so same-origin requests never need CORS at all — the browser
# only consults these headers for *cross*-origin requests. Default to
# allowing none. If a separate frontend origin ever needs to call this
# API directly (e.g. a dev server on a different port, or a future
# standalone frontend deployment), set CORS_ALLOWED_ORIGINS to a
# comma-separated list of exact origins — never "*" once real WO data
# (requester names/emails/phones) is in play.
_cors_origins_env = os.environ.get("CORS_ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,  # auth is via Bearer token, not cookies — no credentialed CORS needed
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(reference.auth_router)
app.include_router(work_orders.router)
app.include_router(dashboard.router)
app.include_router(reference.router)
app.include_router(public.router)
app.include_router(users.router)
app.include_router(admin.router)
app.include_router(task_workers.router)
app.include_router(task_workers.assignable_router)
app.include_router(inventory.router)

# Uploaded photos from the public requester form (see app/routers/public.py)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/", StaticFiles(directory="static", html=True), name="static")
