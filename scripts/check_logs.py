from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.log_service import LogService


service = LogService()
logs = service.list_logs(limit=5)

for log in logs:
    print(f"[{log.level}] {log.source}: {log.message}")
    print(f"  usage: {(log.payload or {}).get('usage')}")
    print()
