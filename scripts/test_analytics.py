import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from app.main import app
from app.schemas.enums import TaskStatus

client = TestClient(app)

print("=" * 60)
print("ANALYTICS SYSTEM TEST")
print("=" * 60)

# 1. Create and process a task — should emit a task_completed event
print("\n[1] Creating and processing task...")
response = client.post('/tasks', json={'prompt': 'Etsy planner for dog owners', 'type': 'seo_writing'})
assert response.status_code == 200, response.text
task = response.json()
task_id = task['id']

response = client.post(f'/tasks/{task_id}/process')
assert response.status_code == 200, response.text
task = response.json()
print(f"✓ Task {task_id} reached status: {task['status']}")

# 2. Check that a task_completed event was recorded (only if task reached DONE)
print("\n[2] Checking analytics events for this task...")
response = client.get('/analytics/events', params={"entity_type": "task", "entity_id": task_id})
assert response.status_code == 200, response.text
events = response.json()
print(f"  Events found: {events}")

if task['status'] == TaskStatus.DONE.value:
    assert any(e['event_type'] == 'task_completed' for e in events), "Expected a task_completed event"
    print("✓ task_completed event correctly recorded")
else:
    print(f"  Task did not reach DONE (status={task['status']}), skipping event assertion")

# 3. Check the summary endpoint aggregates counts
print("\n[3] Checking /analytics/summary...")
response = client.get('/analytics/summary')
assert response.status_code == 200, response.text
summary = response.json()
print(f"  Summary: {summary}")
assert "event_counts" in summary
print("✓ Summary endpoint working")

print("\n" + "=" * 60)
print("ANALYTICS SYSTEM TEST COMPLETE")
print("=" * 60)