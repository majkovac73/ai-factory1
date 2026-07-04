import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.registry import get_agent, list_agents

print("Registered agents:", list_agents())

product_agent = get_agent("product_generator")
product = product_agent.generate_product(
    niche="home decor for cat owners",
    constraints="handmade, ceramic materials preferred"
)
print("\nProduct concept:")
for key, value in product.items():
    print(f"  {key}: {value}")

seo_agent = get_agent("seo_generator")
result = seo_agent.generate_seo(product, task_input="handmade cat-themed home decor")

print("\nSEO generation result:")
print("  valid:", result.get("valid"))
if result.get("valid"):
    for key, value in result["data"].items():
        print(f"  {key}: {value}")
else:
    print("  error:", result.get("error"))