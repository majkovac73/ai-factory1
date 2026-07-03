import os
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

# Create a test task
response = client.post('/tasks', json={'prompt': 'Etsy planner research'})
print('CREATE STATUS', response.status_code)
print('CREATE BODY', response.text)

if response.status_code == 200:
    task_id = response.json().get('id')
    print('TASK ID', task_id)
    response = client.get(f'/tasks/{task_id}')
    print('GET STATUS', response.status_code)
    print('GET BODY', response.text)

    response = client.post(f'/tasks/{task_id}/process')
    print('PROCESS STATUS', response.status_code)
    print('PROCESS BODY', response.text)
else:
    print('Failed to create task; cannot fetch task.')