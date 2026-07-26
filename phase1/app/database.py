"""
Database engine + session setup.

Defaults to a local SQLite file so this runs with zero setup during
development. Set DATABASE_URL to a Postgres URL for staging/prod, e.g.:

    postgresql+psycopg2://user:pass@host:5432/wo_system

SQLite is fine for local iteration but is NOT the target for the real
event — the PRD calls for managed Postgres (Section 9). Don't ship on SQLite.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./wo_system.db")

# Managed Postgres on most PaaS platforms (Railway, Render, Heroku-style)
# hands back a URL starting with "postgres://", which SQLAlchemy rejects —
# it wants "postgresql://" or, for the psycopg2 driver specifically,
# "postgresql+psycopg2://". Normalize both automatically so a copy-pasted
# platform-provided DATABASE_URL just works without hand-editing it.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
