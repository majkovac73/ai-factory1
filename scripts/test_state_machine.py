import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from app.main import app
from app.schemas.enums import TaskStatus

client = TestClient(app)

print("=" * 60)
print("STATE MACHINE ENFORCEMENT TEST")
print("=" * 60)

# 1. Create task
print("\n[1] Creating task in NEW state...")
response = client.post('/tasks', json={'prompt': 'Test task'})
assert response.status_code == 200
task = response.json()
task_id = task['id']
assert task['status'] == TaskStatus.NEW.value
print(f"✓ Task created in NEW state")

# 2. Attempt illegal transition: NEW → QA (should fail)
print("\n[2] Testing illegal transition NEW → QA...")
response = client.patch(f'/tasks/{task_id}/status', json={'status': TaskStatus.QA.value})
assert response.status_code == 422, f"Expected 422, got {response.status_code}"
error = response.json()
assert "Illegal transition" in error['detail'] or "cannot move" in error['detail']
print(f"✓ Illegal transition correctly rejected")
print(f"  Error: {error['detail'][:80]}...")

# 3. Verify task is still in NEW
print("\n[3] Verifying task unchanged after failed transition...")
response = client.get(f'/tasks/{task_id}')
task = response.json()
assert task['status'] == TaskStatus.NEW.value
print(f"✓ Task still in NEW state")

# 4. Legal transition: NEW → PLANNED
print("\n[4] Testing legal transition NEW → PLANNED...")
response = client.patch(f'/tasks/{task_id}/status', json={'status': TaskStatus.PLANNED.value})
assert response.status_code == 200
task = response.json()
assert task['status'] == TaskStatus.PLANNED.value
print(f"✓ Legal transition succeeded")

# 5. Attempt duplicate transition: PLANNED → PLANNED (should fail)
print("\n[5] Testing duplicate transition PLANNED → PLANNED...")
response = client.patch(f'/tasks/{task_id}/status', json={'status': TaskStatus.PLANNED.value})
assert response.status_code == 422
error = response.json()
assert "already in status" in error['detail']
print(f"✓ Duplicate transition correctly rejected")

# 6. Legal transition: PLANNED → RUNNING
print("\n[6] Testing legal transition PLANNED → RUNNING...")
response = client.patch(f'/tasks/{task_id}/status', json={'status': TaskStatus.RUNNING.value})
assert response.status_code == 200
task = response.json()
assert task['status'] == TaskStatus.RUNNING.value
print(f"✓ Legal transition succeeded")

# 7. Legal transition: RUNNING → QA (now legal from RUNNING)
print("\n[7] Testing legal transition RUNNING → QA...")
response = client.patch(f'/tasks/{task_id}/status', json={'status': TaskStatus.QA.value})
assert response.status_code == 200
task = response.json()
assert task['status'] == TaskStatus.QA.value
print(f"✓ Legal transition succeeded")

# 8. Legal transition: QA → DONE
print("\n[8] Testing legal transition QA → DONE...")
response = client.patch(f'/tasks/{task_id}/status', json={'status': TaskStatus.DONE.value})
assert response.status_code == 200
task = response.json()
assert task['status'] == TaskStatus.DONE.value
print(f"✓ Legal transition succeeded")

# 9. Terminal state: DONE → anything (should fail)
print("\n[9] Testing terminal state DONE → FAILED...")
response = client.patch(f'/tasks/{task_id}/status', json={'status': TaskStatus.FAILED.value})
assert response.status_code == 422
error = response.json()
assert "none (terminal state)" in error['detail']
print(f"✓ Terminal state correctly enforced")

print("\n" + "=" * 60)
print("STATE MACHINE ENFORCEMENT TEST PASSED ✓")
print("=" * 60)
print("\nAll legal/illegal state transitions enforced correctly.")