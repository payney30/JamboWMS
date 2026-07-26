import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from .routers import work_orders, dashboard, reference, public
from .routers.public import UPLOAD_DIR

# Schema is managed by Alembic now (see /alembic and README) — run
# `alembic upgrade head` before starting the app. main.py no longer calls
# Base.metadata.create_all(); that call can't apply schema changes to an
# existing database and silently no-ops once a table exists, which is
# exactly the failure mode migrations exist to prevent.

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

# Uploaded photos from the public requester form (see app/routers/public.py)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/", StaticFiles(directory="static", html=True), name="static")
