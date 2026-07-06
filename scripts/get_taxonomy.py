import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from config import settings


async def main():
    api_key_header = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://openapi.etsy.com/v3/application/seller-taxonomy/nodes",
            headers={"x-api-key": api_key_header},
        )
        print(f"Status: {response.status_code}")
        data = response.json()

    def print_node(node, depth=0):
        print(f"{'  ' * depth}id={node['id']}  name={node['name']}")
        for child in node.get("children", []):
            print_node(child, depth + 1)

    for top_level in data.get("results", []):
        print_node(top_level)


asyncio.run(main())