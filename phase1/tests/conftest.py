"""
Shared pytest fixtures.

Each test gets its own throwaway SQLite file (not :memory:, so that the
app-level session — created fresh per request via the overridden get_db
dependency — and the test's own db_session fixture both see the same
data through normal commits, without needing a shared single connection).

Foreign keys are enabled explicitly: SQLite doesn't enforce them by
default, and one of the atomicity tests (test_failed_history_write_rolls_
back_mutation) depends on a FK violation actually raising.
"""
import os
import sys
import tempfile

# Make `app` importable regardless of which directory pytest is invoked from.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# app.routers.public reads UPLOAD_DIR from the environment at import time
# and creates the directory eagerly — set this before app.main (which
# imports it) is imported below, so tests write to a throwaway tempdir
# instead of the real uploads/ folder.
os.environ.setdefault("UPLOAD_DIR", tempfile.mkdtemp(prefix="phase1_test_uploads_"))

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app import models, rate_limit
from app.auth import hash_password
from app.main import app


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """rate_limit's state is module-level and in-memory — clear it between
    tests so one test's failed-login attempts don't leak into the next."""
    rate_limit.reset_all()
    yield
    rate_limit.reset_all()


@pytest.fixture()
def engine(tmp_path):
    db_path = tmp_path / "test.db"
    eng = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=eng)
    yield eng
    Base.metadata.drop_all(bind=eng)


@pytest.fixture()
def SessionLocalTest(engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture()
def db(SessionLocalTest):
    """A session for the test itself to set up fixtures / assert on state."""
    session = SessionLocalTest()
    yield session
    session.close()


@pytest.fixture()
def client(SessionLocalTest):
    """TestClient with get_db overridden to use the isolated test database."""

    def override_get_db():
        session = SessionLocalTest()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _seed_request_types(SessionLocalTest):
    """work_type validation (crud._validate_work_type, PRD 4.5c) now
    checks the request_types table instead of the old hardcoded
    WORK_TYPES tuple — seed the same four standard types every test
    previously got for free from that tuple, so existing tests that pass
    e.g. work_type='NJ Maintenance' keep working without every one of
    them needing its own request_types fixture. Autouse + depends on
    SessionLocalTest (not `db`) so this seeds before either the `db` or
    `client` fixture is used, regardless of which the test declares.

    PRD §4.5e: show_inventory_lookup defaults True for 'NJ Items/Parts'
    here too, matching seed.py's real production seeding — otherwise
    every test relying on the widget-visibility flag would need its own
    one-off setup, and a test asserting "the widget shows for Items/Parts
    by default" would actually be testing this fixture's gap, not the
    app's real default.
    """
    session = SessionLocalTest()
    try:
        for i, name in enumerate(
            ["NJ IT", "NJ Items/Parts", "NJ Maintenance", "NJ Transportation"]
        ):
            session.add(models.RequestType(
                name=name, sort_order=i,
                show_inventory_lookup=(name == "NJ Items/Parts"),
            ))
        session.commit()
    finally:
        session.close()


@pytest.fixture()
def team(db):
    t = models.Team(name="Maintenance Team")
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@pytest.fixture()
def other_team(db):
    t = models.Team(name="IT Team")
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@pytest.fixture()
def asset(db):
    a = models.Asset(name="Campsite A-12", location_group="Branch A")
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


@pytest.fixture()
def admin_user(db):
    u = models.User(
        name="Admin User",
        email="admin@test.local",
        password_hash=hash_password("test-password"),
        role="admin",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture()
def loc_user(db):
    u = models.User(
        name="LOC User",
        email="loc@test.local",
        password_hash=hash_password("test-password"),
        role="loc",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture()
def tech_user(db, team):
    u = models.User(
        name="Tech User",
        email="tech@test.local",
        password_hash=hash_password("test-password"),
        role="tech",
        team_id=team.id,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture()
def tech_user_other_team(db, other_team):
    u = models.User(
        name="Other Team Tech",
        email="tech-other@test.local",
        password_hash=hash_password("test-password"),
        role="tech",
        team_id=other_team.id,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _login(client, email):
    resp = client.post(
        "/auth/login",
        data={"username": email, "password": "test-password"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def auth_headers(client, admin_user):
    return _login(client, admin_user.email)


@pytest.fixture()
def tech_auth_headers(client, tech_user):
    return _login(client, tech_user.email)


@pytest.fixture()
def other_tech_auth_headers(client, tech_user_other_team):
    return _login(client, tech_user_other_team.email)


@pytest.fixture()
def wo_payload(asset):
    return {
        "requester_name": "Scout Leader",
        "requester_email": "leader@example.com",
        "asset_id": asset.id,
        "work_type": "NJ Maintenance",
        "description": "Leaky faucet in the latrine block",
        # Enhancement backlog Phase 14 (PRD §13#15): urgency-tier rename
        # — "Medium" is an old-style value new WOs can no longer use;
        # "Next Day" is its equivalent (both map to a 24h SLA window).
        "priority": "Next Day",
    }
