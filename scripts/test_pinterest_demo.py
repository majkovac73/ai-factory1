"""
Tests for the reviewer-facing Pinterest demo script (scripts/pinterest_demo.py).

The demo is a live, reviewer-recorded script; this covers its PURE, offline
helpers (URL/slug/scope parsing, HTTP tracer redaction, checklist/summary
rendering) and that the file is ASCII-safe (won't crash mid-recording on a
Windows cp1250 console).

Usage: python scripts/test_pinterest_demo.py
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

failures = []
def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


DEMO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pinterest_demo.py")

# 1) ASCII-safe (no char that crashes on a cp1250 console mid-recording)
src = open(DEMO, encoding="utf-8").read()
check("demo file is pure ASCII", all(ord(c) < 128 for c in src))

# 2) imports cleanly (uses the real production modules, no fake layer)
spec = importlib.util.spec_from_file_location("pdemo", DEMO)
pdemo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pdemo)
check("uses real PinterestChannel + pinterest_oauth", hasattr(pdemo, "PinterestChannel") and hasattr(pdemo, "pinterest_oauth"))

# 3) slugify (board URL slug)
check("slugify 'AI Factory Demo' -> ai-factory-demo", pdemo._slugify("AI Factory Demo") == "ai-factory-demo")
check("slugify strips punctuation", pdemo._slugify("My Board!! 2026") == "my-board-2026")

# 4) missing-scope hint extraction from a Pinterest error body
check("scope hint from 'Missing: [pins:write]'", "pins:write" in (pdemo._missing_scope_hint("400: Missing: ['pins:write']") or ""))
check("scope hint from plain text", pdemo._missing_scope_hint("needs boards:write") == "boards:write")
check("no scope hint -> None", pdemo._missing_scope_hint("some other error") is None)

# 5) pin image URL picks the largest
check("pin image url picks largest", pdemo._pin_image_url(
    {"media": {"images": {"150x150": {"url": "small"}, "600x": {"url": "big"}}}}) == "big")
check("pin image url none when absent", pdemo._pin_image_url({"media": {}}) is None)

# 6) HTTP tracer redacts the auth token and shrinks base64 bodies
red = pdemo._redact_headers({"Authorization": "Bearer secret", "Content-Type": "application/json"})
check("tracer redacts Authorization", red["Authorization"] == "Bearer ****REDACTED****" and red["Content-Type"] == "application/json")
shrunk = pdemo._shrink_body({"media_source": {"data": "A" * 5000, "source_type": "image_base64"}})
check("tracer shrinks base64 image data", "base64 chars" in shrunk["media_source"]["data"])

# 7) checklist + summary render without error (pure presentation)
pdemo._USE_COLOR = False
pdemo.SUMMARY.update({"oauth_username": "majkovacai", "sandbox_username": "majkovacai",
                      "board_id": "b1", "pin_id": "1132866481297496924"})
pdemo.mark("OAuth flow completed")
pdemo.phase_checklist()  # must not raise
check("checklist ran and recorded a mark", any("OAuth" in lbl for lbl, _ in pdemo.CHECKLIST))

# 8) demo image is valid base64 PNG
import base64
b64 = pdemo._demo_image_b64()
base64.b64decode(b64)
check("demo image is decodable base64", len(b64) > 50)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All pinterest-demo tests passed.")
