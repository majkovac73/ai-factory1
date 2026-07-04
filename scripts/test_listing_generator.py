import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.registry import get_agent

product_agent = get_agent("product_generator")
product = product_agent.generate_product(
    niche="home decor for cat owners",
    constraints="handmade, ceramic materials preferred"
)
print("PRODUCT:", product.get("product_name"))

seo_agent = get_agent("seo_generator")
seo_result = seo_agent.generate_seo(product, task_input="handmade cat-themed home decor")

if not seo_result.get("valid"):
    print("SEO generation failed:", seo_result.get("error"))
    sys.exit(1)

seo_data = seo_result["data"]
print("SEO TITLE:", seo_data["title"])

listing_agent = get_agent("listing_generator")
listing = listing_agent.generate_listing(product, seo_data)

print("\nFINAL LISTING:")
for key, value in listing.items():
    print(f"  {key}: {value}")