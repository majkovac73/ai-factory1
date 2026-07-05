import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.marketing.base import MarketingChannel
from app.services.marketing_service import MarketingService


class FakeChannel(MarketingChannel):
    name = "fake_channel"

    def post(self, listing: dict) -> dict:
        return {"success": True, "external_id": "fake123", "url": "https://example.com/fake123", "error": None}


service = MarketingService()
result = service.post_to_channel(
    task_id="test-task-id",
    listing={"title": "Test Listing", "price": 20.00},
    channel=FakeChannel(),
)
print("Post result:", result)

posts = service.get_posts_for_task("test-task-id")
for p in posts:
    print(f"  channel={p.channel} status={p.status} external_id={p.external_id}")