"""
Step 104-B test — seasonal listing lifecycle (1-4).

  [1] occasion_for labels a concept by occasion; None for evergreen.
  [2] SeasonalListingService deactivates an OUT-of-window seasonal listing and
      reactivates an IN-window one, tracking seasonal_state, and never re-sends
      the same state.

Usage: python scripts/test_step104_seasonal_lifecycle.py
"""
import os
import sys
import tempfile
from datetime import date
from unittest.mock import patch, AsyncMock

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "seasonal.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine, SessionLocal
from app.models.task import Task
from app.models.image_asset import ImageAsset
from app.core.seasonality import occasion_for

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


Base.metadata.create_all(bind=engine)

# [1] occasion labeling
check("1 valentine concept labeled valentines", occasion_for("Valentine's Day Card", "cute cupid") == "valentines")
check("1 evergreen concept -> None", occasion_for("Boho Wall Art", "cozy print") is None)


def add(task_id, occasion, lid, seasonal_state="active"):
    db = SessionLocal()
    try:
        db.add(Task(id=task_id, prompt="p", type="single_print", status="DONE", input_data={},
                    metadata_={"occasion": occasion, "seasonal_state": seasonal_state}))
        db.add(ImageAsset(task_id=task_id, listing_id=lid, variant="delivery", use_case="delivery",
                          agent="X", local_path=f"/x_{lid}.png"))
        db.commit()
    finally:
        db.close()


# active christmas listing, but window closed (simulate via a fixed 'today' in July)
add("t-xmas", "christmas", "500", seasonal_state="active")
# inactive valentines listing whose window is OPEN (we'll force in_window True)
add("t-val", "valentines", "501", seasonal_state="inactive")

from app.services.seasonal_listing_service import SeasonalListingService

calls = []
async def _fake_update(self, listing_id, fields):  # patched onto the class -> receives self
    calls.append((str(listing_id), fields.get("state")))
    return {"listing_id": listing_id}

# christmas OUT of window, valentines IN window
def fake_in_window(key, today=None):
    return key == "valentines"

with patch("app.services.seasonal_listing_service.occasion_in_window", side_effect=fake_in_window), \
     patch("app.services.etsy_client.EtsyClient.update_listing", new=_fake_update):
    rep = SeasonalListingService().run(apply=True)

states = {lid: st for lid, st in calls}
check("2 christmas listing deactivated (out of window)", states.get("500") == "inactive")
check("2 valentines listing reactivated (in window)", states.get("501") == "active")
check("2 report counts", rep["deactivated"] == 1 and rep["reactivated"] == 1)

# second run: states now match window -> no more API calls
calls.clear()
with patch("app.services.seasonal_listing_service.occasion_in_window", side_effect=fake_in_window), \
     patch("app.services.etsy_client.EtsyClient.update_listing", new=_fake_update):
    rep2 = SeasonalListingService().run(apply=True)
check("2 idempotent: no redundant state changes on second run", len(calls) == 0)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-104-B seasonal-lifecycle tests passed.")
