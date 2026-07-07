"""
Check whether any autonomy_state_*.json files exist on the Railway volume
and print their contents. Run via:
  railway run python scripts/check_autonomy_state.py

If the file for today exists and contains non-zero values that came from a
test run rather than real autonomous operation, it should be deleted — it just
resets today's caps to zero, which is safe (AUTONOMY_ENABLED=False means no
real tasks have been created autonomously).
"""
import sys
import os
import json
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.core.paths import get_data_dir

data_dir = get_data_dir()
print(f"\ndata_dir resolved to: {data_dir}")
print(f"IMAGE_STORAGE_ROOT env: {os.getenv('IMAGE_STORAGE_ROOT', '(not set)')}")
print(f"DATABASE_PATH env:      {os.getenv('DATABASE_PATH', '(not set)')}")

state_files = sorted(data_dir.glob("autonomy_state_*.json"))

if not state_files:
    print("\nNo autonomy_state_*.json files found — volume is clean.")
    sys.exit(0)

print(f"\nFound {len(state_files)} autonomy state file(s):\n")
for f in state_files:
    print(f"  {f}")
    try:
        contents = json.loads(f.read_text(encoding="utf-8"))
        print(f"  Contents: {contents}")
        tasks = contents.get("tasks_created", 0)
        spend = contents.get("spend_usd", 0.0)
        if tasks > 0 or spend > 0.0:
            print(f"  *** NON-ZERO: tasks_created={tasks}, spend_usd={spend}")
            print(f"      Since AUTONOMY_ENABLED=False and no real tasks have run,")
            print(f"      these values came from a test run. Safe to delete.")
        else:
            print(f"  All-zero (either test-created or never-written-to).")
    except Exception as e:
        print(f"  Could not read: {e}")
    print()

print("To delete today's file if it contains test pollution, run:")
print("  railway run python -c \"")
print("  from app.core.paths import get_data_dir")
print("  import datetime, os")
print("  p = get_data_dir() / f'autonomy_state_{datetime.datetime.utcnow().strftime(chr(37)+'Y-'+chr(37)+'m-'+chr(37)+'d')}.json'")
print("  os.unlink(p) if p.exists() else print('not found')")
print("  print('done')\"")
