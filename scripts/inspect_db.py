from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.database import Base, engine
import app.models.task  # noqa: F401

Base.metadata.create_all(bind=engine)
conn = sqlite3.connect(str(Path(__file__).resolve().parents[1] / 'app.db'))
print(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'").fetchall())
print(conn.execute('PRAGMA table_info(tasks)').fetchall())
conn.close()
