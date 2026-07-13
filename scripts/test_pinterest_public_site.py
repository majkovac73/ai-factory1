"""
Pinterest app-review resubmission — public site + privacy policy + disconnect.

Verifies:
  - GET / and GET /privacy return 200 public HTML (no auth), even when
    FACTORY_API_KEY is set (Pinterest's reviewer hits them cold);
  - the privacy policy carries the three required Pinterest-API disclosures;
  - the disconnect flow the policy promises actually deletes the token + Pins.

Usage: python scripts/test_pinterest_public_site.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "pinpub.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine, SessionLocal
from app.models.pinterest_token import PinterestToken
from app.models.marketing_post import MarketingPost
from app.models.task import Task

Base.metadata.create_all(bind=engine)

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)

# ── public pages return 200 HTML ──
r = client.get("/")
check("landing / returns 200", r.status_code == 200)
check("landing is HTML", "text/html" in r.headers.get("content-type", ""))
check("landing names the product", "DesignsForAll" in r.text)
check("landing links to /privacy", "/privacy" in r.text)

p = client.get("/privacy")
check("privacy returns 200", p.status_code == 200)
body = p.text.lower()
check("privacy: Pinterest API disclosure", "pinterest api" in body and "not endorsed" in body and "trademark" in body)
check("privacy: no-resale/redistribution clause", "do not sell" in body and "redistribute" in body)
check("privacy: disconnect + deletion clause", "disconnect" in body and "permanently deleted" in body)
check("privacy: contact email present", "mailto:" in body)

# ── both routes NEVER require a key (public even when FACTORY_API_KEY is set) ──
from app.api.auth import path_requires_key, is_authorized
check("/ never requires a key", path_requires_key("/", "GET") is False)
check("/privacy never requires a key", path_requires_key("/privacy", "GET") is False)
# even with a key configured + none provided, GET / and /privacy are authorized
from config import settings
_orig = settings.FACTORY_API_KEY
settings.FACTORY_API_KEY = "secret-test-key"
try:
    check("/ authorized with key set and none provided", is_authorized("/", "GET", None) is True)
    check("/privacy authorized with key set and none provided", is_authorized("/privacy", "GET", None) is True)
finally:
    settings.FACTORY_API_KEY = _orig

# ── disconnect deletes the token + pinterest posts (the policy's promise) ──
from app.services.pinterest_oauth import disconnect
db = SessionLocal()
db.add(PinterestToken(access_token="a", refresh_token="r", expires_at=datetime.utcnow() + timedelta(hours=1)))
db.add(Task(id="t1", prompt="p", type="single_print", status="DONE", input_data={}))
db.add(MarketingPost(task_id="t1", channel="pinterest", status="success", external_id="pin1"))
db.add(MarketingPost(task_id="t1", channel="tumblr", status="success", external_id="tum1"))
db.commit()
db.close()

res = disconnect()
check("disconnect reports success", res.get("disconnected") is True)
check("disconnect deleted the token", res.get("tokens_deleted") == 1)
check("disconnect deleted the pinterest post", res.get("pinterest_posts_deleted") == 1)

db = SessionLocal()
check("no pinterest token remains", db.query(PinterestToken).count() == 0)
check("pinterest marketing post removed", db.query(MarketingPost).filter(MarketingPost.channel == "pinterest").count() == 0)
check("non-pinterest (tumblr) post is untouched", db.query(MarketingPost).filter(MarketingPost.channel == "tumblr").count() == 1)
db.close()

# idempotent
res2 = disconnect()
check("disconnect is idempotent (0 the second time)", res2.get("tokens_deleted") == 0)

# ── disconnect endpoint is a mutating POST (protected when key set) ──
check("POST /pinterest/disconnect requires a key", path_requires_key("/pinterest/disconnect", "POST") is True)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All Pinterest public-site tests passed.")
