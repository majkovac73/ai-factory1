"""
TumblrChannel unit test — doubles only, zero real API calls, zero generation.

Confirms the channel builds a correct NPF multipart photo post and returns the
standard {success, external_id, url, error} shape MarketingPost expects. A
SEPARATE, one-off real verification post (using already-generated task-127d5130
assets) is done manually only after Maj completes the Tumblr OAuth — it is not
part of this automated suite (same cost-discipline pattern as the other
external-API tests this session).

Covers:
  [1] Happy path with a local PNG: request goes to the right blog/posts URL
      with a Bearer token; the multipart form has a "json" part (NPF body with
      an image block, a text caption block, comma-separated tags, state
      published) and an image part whose field name == the block's media
      identifier. Result: success with external_id + url.
  [2] PDF asset: a .pdf image_path is converted to a PNG first page (no PDF
      bytes sent to Tumblr).
  [3] API failure (401) surfaces as success=False with the error, no raise.

Usage:
  python scripts/test_tumblr_channel.py
"""
import json
import os
import sys
import tempfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")

from PIL import Image as PILImage

import app.marketing.tumblr_channel as tc
from app.marketing.tumblr_channel import TumblrChannel

_passed = _failed = 0


def ok(label):
    global _passed
    _passed += 1
    print(f"  [PASS] {label}")


def fail(label, reason):
    global _failed
    _failed += 1
    print(f"  [FAIL] {label}: {reason}")


print("\nTumblrChannel unit tests (doubles only)\n")


class FakeResponse:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json = json_data
        self.text = json.dumps(json_data)

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("error", request=None, response=self)


class FakeAsyncClient:
    """Captures the outgoing request and returns a canned response."""

    captured = {}

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, files=None, **kw):
        FakeAsyncClient.captured = {"url": url, "headers": headers or {}, "files": files or {}}
        return FakeAsyncClient._response


def _png_path(tmp, name="hero.png", size=(1024, 1024)):
    p = Path(tmp) / name
    PILImage.new("RGB", size, (90, 140, 200)).save(p, format="PNG")
    return p


def _pdf_path(tmp, name="design.pdf", pages=6):
    p = Path(tmp) / name
    imgs = [PILImage.new("RGB", (800, 1200), (40 + i * 20, 90, 160)) for i in range(pages)]
    imgs[0].save(p, format="PDF", save_all=True, append_images=imgs[1:])
    return p


LISTING = {
    "title": "Mindfulness Daily Planner",
    "description": "A calm, minimalist printable daily planner for intentional living.",
    "keywords": ["planner", "mindfulness", "printable", "self care"],
    "listing_url": "https://www.etsy.com/listing/4534803479/mindfulness-daily-planner",
}


def _parse_json_part(files):
    part = files["json"]
    # part is (filename, data, content_type)
    return json.loads(part[1])


# ── [1] happy path, local PNG ────────────────────────────────────────────────
print("[1] happy path (local PNG) builds a correct NPF multipart post...")

with tempfile.TemporaryDirectory() as tmp:
    img = _png_path(tmp)
    listing = dict(LISTING, image_path=str(img))

    FakeAsyncClient._response = FakeResponse(201, {"meta": {"status": 201}, "response": {"id": 12345, "id_string": "12345"}})

    with patch.object(tc, "httpx") as mock_httpx, \
         patch.object(tc, "get_valid_access_token", return_value="fake-token"), \
         patch.object(tc.settings, "TUMBLR_BLOG_NAME", "myblog"):
        # keep the real HTTPStatusError class for raise_for_status
        import httpx as _real_httpx
        mock_httpx.AsyncClient = FakeAsyncClient
        mock_httpx.HTTPStatusError = _real_httpx.HTTPStatusError
        result = TumblrChannel().post(listing)

    cap = FakeAsyncClient.captured
    body = _parse_json_part(cap["files"])
    blocks = body.get("content", [])
    image_block = next((b for b in blocks if b.get("type") == "image"), None)
    text_block = next((b for b in blocks if b.get("type") == "text"), None)
    identifier = image_block["media"][0]["identifier"] if image_block else None

    checks = [
        ("url", "/blog/myblog.tumblr.com/posts" in cap["url"]),
        ("bearer", cap["headers"].get("Authorization") == "Bearer fake-token"),
        ("image block", image_block is not None),
        ("identifier maps to a form part", identifier in cap["files"]),
        ("media mime png", image_block and image_block["media"][0]["type"] == "image/png"),
        ("width/height set", image_block and image_block["media"][0].get("width") == 1024),
        ("text caption block", text_block is not None and "Mindfulness Daily Planner" in text_block["text"]),
        ("listing link in caption", text_block is not None and "https://www.etsy.com/listing/4534803479" in text_block["text"]),
        ("tags comma-separated", body.get("tags") == "planner,mindfulness,printable,self care"),
        ("state published", body.get("state") == "published"),
        ("result success", result.get("success") is True),
        ("external_id", result.get("external_id") == "12345"),
        ("url built", result.get("url") == "https://myblog.tumblr.com/post/12345"),
    ]
    bad = [n for n, c in checks if not c]
    if not bad:
        ok("[1] correct NPF multipart request + success result")
    else:
        fail("[1] happy path", f"failed checks: {bad}; body={body}; result={result}")


# ── [2] PDF asset converted to PNG ───────────────────────────────────────────
print("[2] a .pdf asset is converted to a PNG first page (no PDF bytes sent)...")

with tempfile.TemporaryDirectory() as tmp:
    pdf = _pdf_path(tmp)
    listing = dict(LISTING, image_path=str(pdf))

    FakeAsyncClient._response = FakeResponse(201, {"response": {"id_string": "999"}})

    with patch.object(tc, "httpx") as mock_httpx, \
         patch.object(tc, "get_valid_access_token", return_value="fake-token"), \
         patch.object(tc.settings, "TUMBLR_BLOG_NAME", "myblog"):
        import httpx as _real_httpx
        mock_httpx.AsyncClient = FakeAsyncClient
        mock_httpx.HTTPStatusError = _real_httpx.HTTPStatusError
        result = TumblrChannel().post(listing)

    cap = FakeAsyncClient.captured
    body = _parse_json_part(cap["files"])
    image_block = next((b for b in body["content"] if b.get("type") == "image"), None)
    ident = image_block["media"][0]["identifier"]
    sent_filename, sent_bytes, sent_mime = cap["files"][ident]
    # Confirm the uploaded bytes are a real decodable PNG, not raw PDF bytes.
    decodable_png = False
    try:
        with PILImage.open(BytesIO(sent_bytes)) as im:
            decodable_png = im.format == "PNG"
    except Exception:
        decodable_png = False

    if result.get("success") and sent_mime == "image/png" and decodable_png:
        ok("[2] PDF first page extracted and uploaded as a valid PNG")
    else:
        fail("[2] pdf asset", f"success={result.get('success')}, mime={sent_mime}, decodable_png={decodable_png}")


# ── [3] API failure surfaces cleanly ─────────────────────────────────────────
print("[3] a 401 from Tumblr surfaces as success=False with the error...")

with tempfile.TemporaryDirectory() as tmp:
    img = _png_path(tmp)
    listing = dict(LISTING, image_path=str(img))

    FakeAsyncClient._response = FakeResponse(401, {"meta": {"status": 401, "msg": "Unauthorized"}})

    with patch.object(tc, "httpx") as mock_httpx, \
         patch.object(tc, "get_valid_access_token", return_value="fake-token"), \
         patch.object(tc.settings, "TUMBLR_BLOG_NAME", "myblog"):
        import httpx as _real_httpx
        mock_httpx.AsyncClient = FakeAsyncClient
        mock_httpx.HTTPStatusError = _real_httpx.HTTPStatusError
        result = TumblrChannel().post(listing)

    if result.get("success") is False and result.get("external_id") is None and "401" in (result.get("error") or ""):
        ok("[3] API failure returned cleanly (no raise), error surfaced")
    else:
        fail("[3] api failure", f"result={result}")


# ── [4] no listing link -> "store in bio" fallback in caption ────────────────
print("[4] with no listing link, caption falls back to a store-in-bio pointer...")

with tempfile.TemporaryDirectory() as tmp:
    img = _png_path(tmp)
    listing = {k: v for k, v in LISTING.items() if k != "listing_url"}
    listing["image_path"] = str(img)  # no listing_url / product_url

    FakeAsyncClient._response = FakeResponse(201, {"response": {"id_string": "555"}})

    with patch.object(tc, "httpx") as mock_httpx, \
         patch.object(tc, "get_valid_access_token", return_value="fake-token"), \
         patch.object(tc.settings, "TUMBLR_BLOG_NAME", "myblog"):
        import httpx as _real_httpx
        mock_httpx.AsyncClient = FakeAsyncClient
        mock_httpx.HTTPStatusError = _real_httpx.HTTPStatusError
        result = TumblrChannel().post(listing)

    body = _parse_json_part(FakeAsyncClient.captured["files"])
    text_block = next((b for b in body["content"] if b.get("type") == "text"), None)
    caption = text_block["text"] if text_block else ""
    if result.get("success") and "Etsy store in bio" in caption and "Shop this:" not in caption:
        ok("[4] caption includes store-in-bio fallback when no link is present")
    else:
        fail("[4] fallback link", f"caption={caption!r}, result={result}")


print(f"\nResults: {_passed} passed, {_failed} failed\n")
sys.exit(0 if _failed == 0 else 1)
