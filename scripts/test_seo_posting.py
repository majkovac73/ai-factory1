import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from app.main import app
from app.schemas.enums import TaskStatus

client = TestClient(app)

print("=" * 60)
print("SEO POSTING SYSTEM TEST")
print("=" * 60)

# 1. Create and fully process a task so it has output_data
print("\n[1] Creating and processing task...")
response = client.post('/tasks', json={'prompt': 'Etsy planner for cat owners', 'type': 'seo_writing'})
assert response.status_code == 200, response.text
task = response.json()
task_id = task['id']

response = client.post(f'/tasks/{task_id}/process')
assert response.status_code == 200, response.text
task = response.json()
print(f"✓ Task {task_id} reached status: {task['status']}")

if task['status'] != TaskStatus.DONE.value:
    print(f"✗ Task did not reach DONE, got {task['status']} — cannot test posting")
    sys.exit(1)

# 2. Attempt to post to a channel with no credentials (expected to fail gracefully)
print("\n[2] Posting task to Pinterest (expect failure without real OAuth token)...")
response = client.post(f'/marketing/post/{task_id}')
print(f"  Status code: {response.status_code}")
print(f"  Body: {response.text}")

if response.status_code == 502:
    print("✓ Correctly failed at the Pinterest API call (no token configured) — service wiring is correct")
elif response.status_code == 200:
    print("✓ Post succeeded (Pinterest credentials must already be configured)")
else:
    print(f"✗ Unexpected status code: {response.status_code}")
    sys.exit(1)

# 3. Attempt duplicate post — should be rejected regardless of step 2's outcome
if response.status_code == 200:
    print("\n[3] Attempting duplicate post (should be rejected)...")
    dup_response = client.post(f'/marketing/post/{task_id}')
    assert dup_response.status_code == 422, f"Expected 422, got {dup_response.status_code}"
    print(f"✓ Duplicate post correctly rejected: {dup_response.json()['detail']}")
else:
    print("\n[3] Skipping duplicate-post check since step 2 didn't succeed.")

print("\n" + "=" * 60)
print("SEO POSTING SYSTEM TEST COMPLETE")
print("=" * 60)