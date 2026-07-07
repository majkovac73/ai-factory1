import os
from pathlib import Path


def get_data_dir() -> Path:
    """
    Returns the durable data directory for state files.

    Resolution order:
      1. Parent of IMAGE_STORAGE_ROOT if set  (e.g. /data/images -> /data)
      2. Parent of DATABASE_PATH if set       (e.g. /data/app.db  -> /data)
      3. <project_root>/data  (local dev default)
    """
    image_root = os.getenv("IMAGE_STORAGE_ROOT")
    if image_root:
        return Path(image_root).parent

    db_path = os.getenv("DATABASE_PATH")
    if db_path:
        return Path(db_path).parent

    return Path(__file__).resolve().parents[2] / "data"
