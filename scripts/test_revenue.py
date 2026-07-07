import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from app.main import app
from app.schemas.enums import TaskStatus

client = TestClient(app)

print("=" * 60)
print("REVENUE TRACKING TEST")
print("=" * 60)

# 1. Create and process a task so we have a real task_id to attach revenue to
print("\n[1] Creating and processing task...")
response = client.post('/tasks', json={'prompt': 'Etsy planner for bird owners', 'type': 'seo_writing'})
assert response.status_code == 200, response.text
task = response.json()
task_id = task['id']

response = client.post(f'/tasks/{task_id}/process')
assert response.status_code == 200, response.text
task = response.json()
print(f"✓ Task {task_id} reached status: {task['status']}")

# 2. Record a sale against this task
print("\n[2] Recording a sale...")
response = client.post('/analytics/revenue', json={
    "task_id": task_id,
    "amount": 24.99,
    "currency": "USD",
    "quantity": 1,
    "notes": "Sold via Etsy search",
})
assert response.status_code == 200, response.text
print(f"✓ Sale recorded: {response.json()}")

# 3. Record a second sale for the same task (multiple sales of same product)
print("\n[3] Recording a second sale for the same task...")
response = client.post('/analytics/revenue', json={
    "task_id": task_id,
    "amount": 24.99,
    "quantity": 1,
})
assert response.status_code == 200, response.text
print("✓ Second sale recorded")

# 4. Check revenue summary for this specific task
print("\n[4] Checking revenue summary for task...")
response = client.get('/analytics/revenue/summary', params={"task_id": task_id})
assert response.status_code == 200, response.text
summary = response.json()
print(f"  Summary: {summary}")
assert summary["sale_count"] == 2, f"Expected 2 sales, got {summary['sale_count']}"
assert abs(summary["total_revenue"] - 49.98) < 0.01, f"Expected ~49.98, got {summary['total_revenue']}"
print("✓ Revenue summary correct")

# 5. Check revenue-by-task breakdown includes this task
print("\n[5] Checking revenue-by-task breakdown...")
response = client.get('/analytics/revenue/by-task')
assert response.status_code == 200, response.text
breakdown = response.json()
assert task_id in breakdown, f"Expected {task_id} in breakdown"
print(f"✓ Task {task_id} appears in breakdown with revenue {breakdown[task_id]}")

# 6. Reject invalid sale (negative amount)
print("\n[6] Testing rejection of invalid amount...")
response = client.post('/analytics/revenue', json={
    "task_id": task_id,
    "amount": -5.00,
})
assert response.status_code == 422, f"Expected 422, got {response.status_code}"
print("✓ Negative amount correctly rejected")

print("\n" + "=" * 60)
print("REVENUE TRACKING TEST COMPLETE")
print("=" * 60)