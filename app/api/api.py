from fastapi import APIRouter

from app.api.routes import health, tasks, logs, dashboard, etsy

api_router = APIRouter()

api_router.include_router(tasks.router, prefix="/tasks", tags=["Tasks"])
api_router.include_router(health.router, prefix="/health", tags=["Health"])
api_router.include_router(logs.router, prefix="/logs", tags=["Logs"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["Dashboard"])
api_router.include_router(etsy.router, prefix="/etsy", tags=["Etsy"])