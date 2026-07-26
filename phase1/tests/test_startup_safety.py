"""
Tests the startup guard in app/main.py that refuses to boot against a
real (non-SQLite) database while still using the fallback dev JWT secret.
Run as subprocesses — app.main is already imported (and cached) by the
rest of the test suite via conftest.py, so re-importing it in-process
with different env vars wouldn't re-execute the module-level check.
"""
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_BOOT_SNIPPET = "from app.main import app; print('BOOTED')"


def _try_boot(env_overrides: dict) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", _BOOT_SNIPPET],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_boots_locally_with_sqlite_and_no_jwt_secret():
    env = {k: v for k, v in {"DATABASE_URL": None, "JWT_SECRET": None}.items()}
    # subprocess env can't contain None — just omit these keys entirely
    result = _try_boot({})
    assert result.returncode == 0, result.stderr
    assert "BOOTED" in result.stdout


def test_refuses_to_boot_against_postgres_with_dev_secret():
    result = _try_boot({"DATABASE_URL": "postgres://user:pass@host:5432/db"})
    assert result.returncode != 0
    assert "Refusing to start" in result.stderr
    assert "JWT_SECRET" in result.stderr


def test_boots_against_postgres_url_with_real_secret_set():
    # Only checks it gets past the startup guard — it will still fail to
    # actually connect (no real Postgres in this environment), which is
    # fine; we're testing the guard, not full connectivity.
    result = _try_boot({
        "DATABASE_URL": "postgresql+psycopg2://user:pass@localhost:59999/doesnotexist",
        "JWT_SECRET": "a-real-random-secret-value",
    })
    assert "Refusing to start" not in result.stderr
