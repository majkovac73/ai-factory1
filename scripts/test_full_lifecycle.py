import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from app.main import app
from app.schemas.enums import TaskStatus

client = TestClient(app)

print("=" * 60)
print("FULL TASK LIFECYCLE TEST: NEW → PLANNED → RUNNING → QA → DONE")
print("=" * 60)

# 1. Create task (should be NEW)
print("\n[STEP 1] Creating task...")
response = client.post('/tasks', json={'prompt': 'Etsy planner research'})
assert response.status_code == 200, f"Failed to create task: {response.text}"
task = response.json()
task_id = task['id']
assert task['status'] == TaskStatus.NEW.value, f"Expected NEW, got {task['status']}"
print(f"✓ Task created with ID {task_id}")
print(f"  Status: {task['status']}")

# 2. Fetch task to confirm NEW
print("\n[STEP 2] Confirming task is in NEW state...")
response = client.get(f'/tasks/{task_id}')
assert response.status_code == 200
task = response.json()
assert task['status'] == TaskStatus.NEW.value
print(f"✓ Task confirmed in NEW state")

# 3. Process task (runs through PLANNED → RUNNING → QA → DONE)
print("\n[STEP 3] Processing task (PLANNED → RUNNING → QA → DONE)...")
response = client.post(f'/tasks/{task_id}/process')
assert response.status_code == 200, f"Processing failed: {response.text}"
task = response.json()
print(f"✓ Task processing completed")
print(f"  Final status: {task['status']}")

# 4. Validate final state is DONE
print("\n[STEP 4] Validating final state...")
assert task['status'] == TaskStatus.DONE.value, f"Expected DONE, got {task['status']}"
print(f"✓ Task reached DONE state")

# 5. Validate output_data schema
print("\n[STEP 5] Validating output schema...")
output = task['output_data']
assert output is not None, "output_data is None"
assert isinstance(output, dict), f"output_data is not a dict: {type(output)}"
assert 'title' in output, "Missing 'title' field"
assert 'description' in output, "Missing 'description' field"
assert 'keywords' in output, "Missing 'keywords' field"
assert 'sections' in output, "Missing 'sections' field"
assert isinstance(output['keywords'], list), "keywords is not a list"
assert isinstance(output['sections'], list), "sections is not a list"
assert len(output['keywords']) > 0, "keywords list is empty"
assert len(output['sections']) > 0, "sections list is empty"
print(f"✓ Output schema valid")
print(f"  Title: {output['title'][:50]}...")
print(f"  Keywords: {', '.join(output['keywords'][:3])}...")
print(f"  Sections: {len(output['sections'])} structured sections")

# 6. Validate no errors
print("\n[STEP 6] Validating error state...")
assert task['error_message'] is None, f"Task has error: {task['error_message']}"
print(f"✓ No errors recorded")

print("\n" + "=" * 60)
print("FULL LIFECYCLE TEST PASSED ✓")
print("=" * 60)
print(f"\nSummary:")
print(f"  Task ID: {task_id}")
print(f"  Lifecycle: NEW → PLANNED → RUNNING → QA → DONE")
print(f"  Output: Valid SEO schema with {len(output['sections'])} sections")
print(f"  Errors: None")