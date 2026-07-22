"""
Image-generation transient-retry test.

A one-off Seedream 520 (or 429/network blip) must NOT block an otherwise-good
product: image generation has no side effect, so the provider retries. A real
4xx (bad request) must still fail fast.

Usage: python scripts/test_image_gen_retry.py
"""
import os, sys, asyncio
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

failures = []
def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


class FakeResp:
    def __init__(self, status, body="", js=None):
        self.status_code = status
        self.text = body
        self._js = js
    def json(self):
        return self._js


class FakeClient:
    """AsyncClient stand-in; pops from a SHARED queue so each `async with`
    (one per retry attempt) advances through the responses in order."""
    def __init__(self, state):
        self._state = state  # {"queue": [...], "calls": 0}
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def post(self, *a, **k):
        i = self._state["calls"]
        self._state["calls"] += 1
        r = self._state["queue"][i]
        if isinstance(r, Exception):
            raise r
        return r


from app.core.providers.openrouter_image_provider import OpenRouterImageProvider

GOOD = FakeResp(200, js={"data": [{"b64_json": "aGk="}], "usage": {}})


def run_with(responses):
    prov = OpenRouterImageProvider.__new__(OpenRouterImageProvider)
    prov._api_key = "k"
    prov._model = "test/model"
    prov._record_image_spend = lambda *a, **k: None
    state = {"queue": list(responses), "calls": 0}
    holder = {"state": state}
    def _factory(*a, **k):
        return FakeClient(state)
    # no real sleeping
    async def _no_sleep(*a, **k):
        return None
    with patch("httpx.AsyncClient", _factory), \
         patch.object(settings, "IMAGE_GEN_MAX_ATTEMPTS", 4), \
         patch("asyncio.sleep", _no_sleep), \
         patch("app.services.autonomy_service.AutonomyService.assert_within_circuit_breaker",
               lambda self: None):
        result = asyncio.new_event_loop().run_until_complete(
            prov.generate_image("a prompt")
        )
    return result, holder


# 1) transient 520 then success -> recovers, no exception
res, st = run_with([FakeResp(520, "upstream"), GOOD])
check("520 then 200 -> recovers", res is not None and res.b64_data == "aGk=")
check("520 then 200 -> retried exactly once", st["state"]["calls"] == 2)

# 2) 429 rate-limit then success -> recovers
res, st = run_with([FakeResp(429, "slow down"), GOOD])
check("429 then 200 -> recovers", res is not None)

# 3) network blip (exception) then success -> recovers
res, st = run_with([ConnectionError("reset"), GOOD])
check("network error then 200 -> recovers", res is not None)

# 4) persistent 520 -> raises after all attempts (does not hang / succeed)
raised = False
try:
    run_with([FakeResp(520, "down")] * 4)
except RuntimeError as e:
    raised = "after 4 attempts" in str(e)
check("persistent 520 -> raises after 4 attempts", raised)

# 5) real 400 -> fails FAST, no retry (bad request won't fix itself)
calls_seen = {}
try:
    res, st = run_with([FakeResp(400, "bad prompt"), GOOD])
    calls_seen["n"] = st["state"]["calls"]
except RuntimeError:
    calls_seen["n"] = 1
check("400 -> fails fast without retry", calls_seen.get("n") == 1)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All image-gen retry tests passed.")
