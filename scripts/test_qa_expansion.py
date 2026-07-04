import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.registry import get_agent, list_agents

print("Registered agents:", list_agents())

sample_output = {
    "title": "Handmade Ceramic Mug",
    "description": "A lovely handcrafted mug made from premium clay.",
    "keywords": ["mug", "ceramic", "handmade"],
    "sections": ["Intro", "Details"],
}

consistency = get_agent("consistency")
result = consistency.check(sample_output)
print("\nConsistencyAgent result:", result)

fact_check = get_agent("fact_check")
result = fact_check.check(sample_output, "Write a description for a handmade ceramic mug")
print("\nFactCheckAgent result:", result)

completeness = get_agent("completeness")
result = completeness.check(sample_output, "Write a description for a handmade ceramic mug, mention it's dishwasher safe")
print("\nCompletenessAgent result:", result)