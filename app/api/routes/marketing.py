from fastapi import APIRouter, HTTPException, Query

from app.services.seo_posting_service import SEOPostingService

router = APIRouter()
seo_posting_service = SEOPostingService()


@router.post("/post/{task_id}")
def post_task_to_channel(task_id: str, channel: str = Query(default="pinterest")):
    """
    Posts a DONE task's validated SEO output (title/description/keywords)
    to the given marketing channel. Defaults to Pinterest.
    """
    try:
        result = seo_posting_service.post_task_seo(task_id, channel_name=channel)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if not result.get("success"):
        raise HTTPException(
            status_code=502,
            detail=f"Posting to '{channel}' failed: {result.get('error')}",
        )

    return result