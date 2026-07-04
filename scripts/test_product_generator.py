import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.registry import get_agent, list_agents

print("Registered agents:", list_agents())

agent = get_agent("product_generator")
result = agent.generate_product(
    niche="home decor for cat owners",
    constraints="handmade, ceramic materials preferred"
)

print("\nProductGeneratorAgent result:")
for key, value in result.items():
    print(f"  {key}: {value}")