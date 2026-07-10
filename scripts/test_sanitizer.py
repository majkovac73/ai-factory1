import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.utils.json_sanitizer import JSONSanitizer

s = JSONSanitizer()

texts = [
    '{"title": "One", "description": "A"}{"title": "Two"}',
    'Some preamble text\n```json\n{"title":"Good","keywords":["x"]}\n```\n',
    '',
]

for i, t in enumerate(texts, 1):
    print(f"--- test {i} ---")
    try:
        parsed = s.extract(t)
        print('PARSED:', parsed)
    except Exception as e:
        print('ERROR:', e)
