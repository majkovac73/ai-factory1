"""
Step 104 test — 7-1 wall_art_set_3 format (validator exemption, gating,
deterministic set helpers).

Usage: python scripts/test_step104_wall_art_set.py
"""
import os
import sys
import tempfile

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "was.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image
from config import settings

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# ── format registered ──
from app.core.product_formats import PRODUCT_FORMATS, materials_for, description_blocks
spec = PRODUCT_FORMATS.get("wall_art_set_3")
check("7-1 wall_art_set_3 registered", spec is not None)
check("7-1 delivery is image_set", spec.get("delivery") == "image_set")
check("7-1 set_size is 3", spec.get("set_size") == 3)
check("7-1 higher AOV band (>=8)", spec.get("price_band", (0, 0))[0] >= 8.0)
check("7-1 has materials", len(materials_for("wall_art_set_3")) >= 1)
check("7-1 description mentions a set of 3", "3" in description_blocks("wall_art_set_3"))

# ── validator: multi-item exemption ONLY for the set ──
from app.agents.trend_research_agent import TrendResearchAgent
agent = TrendResearchAgent.__new__(TrendResearchAgent)  # skip __init__ (no provider)

set_concept = {
    "product_name": "Desert Sunset Wall Art Set of 3",
    "product_format": "wall_art_set_3",
    "description": "A coordinated Desert Sunset Wall Art Set of 3 matching boho prints for a gallery wall.",
    "target_audience": "boho home decorators",
}
# with the flag ON, the set concept must validate despite 'set of 3' wording
settings.WALL_ART_SET_ENABLED = True
err = agent._validate_product(set_concept)
check(f"7-1 set concept passes when enabled (err={err})", err is None)

# 'set of 3' on a NON-set format is still rejected
bad = dict(set_concept, product_format="single_print",
           description="A single_print with a set of 3 assorted images.",
           product_name="Random Set of 3 Prints")
err2 = agent._validate_product(bad)
check("7-1 'set of 3' still banned for single_print", err2 is not None and "multiple" in err2.lower())

# with the flag OFF, wall_art_set_3 is paused
settings.WALL_ART_SET_ENABLED = False
err3 = agent._validate_product(set_concept)
check("7-1 format paused when flag off", err3 is not None and "paused" in err3.lower())
check("7-1 _proposable_formats excludes set when off",
      "wall_art_set_3" not in TrendResearchAgent._proposable_formats())
settings.WALL_ART_SET_ENABLED = True
check("7-1 _proposable_formats includes set when on",
      "wall_art_set_3" in TrendResearchAgent._proposable_formats())

# ── deterministic set helpers ──
from app.services.wall_art_set_service import WallArtSetService, SET_SIZE

briefs = WallArtSetService.piece_briefs("Desert Sunset Trio", "warm terracotta desert landscapes")
check("7-1 piece_briefs returns 3", len(briefs) == SET_SIZE)
check("7-1 briefs demand a shared palette", all("same" in b.lower() and "palette" in b.lower() for b in briefs))
check("7-1 briefs are distinct (different roles)", len(set(briefs)) == 3)

tmp = tempfile.mkdtemp()
# 3 matching (warm) pieces + 1 clashing (cold blue) piece
warm = [(210, 120, 60), (200, 110, 55), (215, 130, 70)]
paths = []
for i, c in enumerate(warm):
    p = os.path.join(tmp, f"warm{i}.png")
    Image.new("RGB", (512, 512), c).save(p)
    paths.append(p)
cold = os.path.join(tmp, "cold.png")
Image.new("RGB", (512, 512), (40, 60, 200)).save(cold)

con = WallArtSetService.palette_consistent(paths, tol=0.42)
check("7-1 matching pieces are palette-consistent", con["consistent"] is True)
clash = WallArtSetService.palette_consistent([paths[0], paths[1], cold], tol=0.42)
check("7-1 a clashing piece is flagged inconsistent", clash["consistent"] is False)

trip = os.path.join(tmp, "triptych.png")
WallArtSetService.compose_triptych(paths, trip)
check("7-1 triptych composed", os.path.exists(trip))
w, h = Image.open(trip).size
check("7-1 triptych is landscape (3 side-by-side)", w > h)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-104 (7-1) tests passed.")
