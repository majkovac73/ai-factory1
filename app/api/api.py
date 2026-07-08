from fastapi import APIRouter

from app.api.routes import health, tasks, logs, dashboard, etsy, pinterest, tumblr, marketing, analytics, pod

api_router = APIRouter()

api_router.include_router(tasks.router, prefix="/tasks", tags=["Tasks"])
api_router.include_router(health.router, prefix="/health", tags=["Health"])
api_router.include_router(logs.router, prefix="/logs", tags=["Logs"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["Dashboard"])
api_router.include_router(etsy.router, prefix="/etsy", tags=["Etsy"])
api_router.include_router(pinterest.router, prefix="/pinterest", tags=["Pinterest"])
api_router.include_router(tumblr.router, prefix="/tumblr", tags=["Tumblr"])
api_router.include_router(marketing.router, prefix="/marketing", tags=["Marketing"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["Analytics"])
api_router.include_router(pod.router, prefix="/pod", tags=["POD"])