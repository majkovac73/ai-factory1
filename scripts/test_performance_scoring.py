import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from app.main import app
from app.schemas.enums import TaskStatus

client = TestClient(app)

print("=" * 60)
print("PERFORMANCE SCORING TEST")
print("=" * 60)

# 1. Create and process a task
print("\n[1] Creating and processing task...")
response = client.post('/tasks', json={'prompt': 'Etsy planner for hamster owners', 'type': 'seo_writing'})
assert response.status_code == 200, response.text
task = response.json()
task_id = task['id']

response = client.post(f'/tasks/{task_id}/process')
assert response.status_code == 200, response.text
task = response.json()
print(f"✓ Task {task_id} reached status: {task['status']}")

# 2. Record a sale for this task so revenue points are non-zero
print("\n[2] Recording a sale against the task...")
response = client.post('/analytics/revenue', json={
    "task_id": task_id,
    "amount": 30.00,
})
assert response.status_code == 200, response.text
print("✓ Sale recorded")

# 3. Fetch performance score for this specific task
print("\n[3] Fetching performance score for task...")
response = client.get(f'/analytics/performance/{task_id}')
assert response.status_code == 200, response.text
score = response.json()
print(f"  Score: {score}")
assert score["task_id"] == task_id
assert 0 <= score["score"] <= 100, f"Score out of range: {score['score']}"
assert "revenue_points" in score["breakdown"]
assert "reliability_points" in score["breakdown"]
assert "marketing_points" in score["breakdown"]
print("✓ Task performance score is well-formed and in range")

# 4. Fetch performance score for a non-existent task (should 404)
print("\n[4] Testing 404 for unknown task...")
response = client.get('/analytics/performance/does-not-exist')
assert response.status_code == 404, f"Expected 404, got {response.status_code}"
print("✓ Unknown task correctly returns 404")

# 5. Fetch all performance scores, sorted descending
print("\n[5] Fetching all performance scores...")
response = client.get('/analytics/performance')
assert response.status_code == 200, response.text
all_scores = response.json()
assert isinstance(all_scores, list)
assert any(s["task_id"] == task_id for s in all_scores), "Expected our task in the full list"
if len(all_scores) > 1:
    scores_only = [s["score"] for s in all_scores]
    assert scores_only == sorted(scores_only, reverse=True), "Scores are not sorted descending"
print(f"✓ Retrieved {len(all_scores)} scored task(s), correctly sorted")

print("\n" + "=" * 60)
print("PERFORMANCE SCORING TEST COMPLETE")
print("=" * 60)