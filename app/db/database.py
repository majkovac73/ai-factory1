import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = BASE_DIR / "app.db"

# DATABASE_PATH takes precedence (Railway: /data/app.db).
# Falls back to DATABASE_URL env var, then the local default.
_db_path = os.getenv("DATABASE_PATH")
if _db_path:
    DATABASE_URL = f"sqlite:///{_db_path}"
else:
    DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH}")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


# P2-1: TaskWorker, EtsyReceiptWorker, AutonomyWorker, MarketingRefreshWorker
# and API request threads all read/write this SQLite file. Without WAL + a busy
# timeout, overlapping writes throw "database is locked". WAL lets readers and a
# writer coexist; busy_timeout makes a blocked writer wait instead of failing
# immediately. Applied per-connection (SQLite only).
if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

Base = declarative_base()