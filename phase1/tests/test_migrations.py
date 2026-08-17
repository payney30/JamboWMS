"""
Guards against the exact failure this migration setup exists to prevent:
`alembic upgrade head` against a brand-new database must succeed and
produce every table the ORM models declare. Run this whenever a model
changes and a new revision is added, to catch a migration that doesn't
actually apply cleanly before it ships.
"""
import os
import subprocess
import sys

from sqlalchemy import create_engine, inspect

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXPECTED_TABLES = {
    "assets",
    "teams",
    "users",
    "work_orders",
    "wo_notes",
    "wo_status_history",
    "wo_attachments",
    "response_templates",
    "inventory_items",
    "wo_suggested_supplies",
    "alembic_version",
}


def test_alembic_upgrade_head_creates_full_schema(tmp_path):
    db_path = tmp_path / "migration_smoke_test.db"
    db_url = f"sqlite:///{db_path}"

    env = os.environ.copy()
    env["DATABASE_URL"] = db_url

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic upgrade failed:\n{result.stderr}"

    engine = create_engine(db_url)
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES.issubset(tables)
