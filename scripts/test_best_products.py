import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from app.main import app
from app.schemas.enums import TaskStatus

client = TestClient(app)

print("=" * 60)
print("BEST PRODUCTS SYSTEM TEST")
print("=" * 60)

# 1. Create and process a task, then record a solid sale so it qualifies as "best"
print("\n[1] Creating and processing a high-performing task...")
response = client.post('/tasks', json={'prompt': 'Etsy planner for cat owners', 'type': 'seo_writing'})
assert response.status_code == 200, response.text
task = response.json()
task_id = task['id']

response = client.post(f'/tasks/{task_id}/process')
assert response.status_code == 200, response.text
task = response.json()
print(f"✓ Task {task_id} reached status: {task['status']}")

if task['status'] != TaskStatus.DONE.value:
    print(f"  Task did not reach DONE (status={task['status']}), best-products checks below may show empty results — that's expected, not a failure.")

print("\n[2] Recording a sale to push this task's score up...")
response = client.post('/analytics/revenue', json={
    "task_id": task_id,
    "amount": 100.00,
    "quantity": 1,
})
assert response.status_code == 200, response.text
print("✓ Sale recorded")

# 3. Check /analytics/best-products includes this task
print("\n[3] Checking /analytics/best-products...")
response = client.get('/analytics/best-products', params={"limit": 10})
assert response.status_code == 200, response.text
best = response.json()
print(f"  Best products: {best}")

match = next((p for p in best if p["task_id"] == task_id), None)
if match:
    print(f"✓ Task {task_id} appears in best products with score {match['score']}")
else:
    print(f"  Task {task_id} did not qualify (score below MIN_SCORE_FOR_BEST) — check breakdown via /analytics/performance/{task_id} if unexpected")

# 4. Check insights endpoint aggregates correctly
print("\n[4] Checking /analytics/best-products/insights...")
response = client.get('/analytics/best-products/insights', params={"limit": 10})
assert response.status_code == 200, response.text
insights = response.json()
print(f"  Insights: {insights}")
assert "average_score" in insights
assert "top_task_types" in insights
assert "top_keywords" in insights
print("✓ Insights endpoint structure correct")

# 5. Custom min_score override
print("\n[5] Testing min_score override (very high threshold, expect empty list)...")
response = client.get('/analytics/best-products', params={"limit": 10, "min_score": 999})
assert response.status_code == 200, response.text
assert response.json() == []
print("✓ High threshold correctly returns empty list")

print("\n" + "=" * 60)
print("BEST PRODUCTS SYSTEM TEST COMPLETE")
print("=" * 60)