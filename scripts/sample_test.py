import os
import json
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

# Create a test task
response = client.post('/task', params={'task_type': 'seo_writing', 'input': 'Etsy planner research'})
print('CREATE STATUS', response.status_code)
print('CREATE BODY', response.text)

if response.status_code == 200:
    task_id = response.json().get('task_id')
    print('TASK ID', task_id)
    response = client.get(f'/task/{task_id}')
    print('GET STATUS', response.status_code)
    print('GET BODY', response.text)
else:
    print('Failed to create task; cannot fetch task.')
