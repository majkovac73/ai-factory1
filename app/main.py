import logging

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from app.api.tasks import router as task_router
from config import settings
from database.base import Base
from database.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai-factory")

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="AI Business Automation Platform",
    version="1.0.0",
    description="Automated AI task orchestration system",
    debug=settings.DEBUG,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "ai-factory1",
    }


@app.on_event("startup")
async def startup_event():
    logger.info("AI Factory server starting...")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("AI Factory server shutting down...")


# Future router registration hook
# from app.api.task_routes import router as task_router
# app.include_router(task_router, prefix="/task", tags=["Task"])

app.include_router(task_router)

logger.info("AI Factory API initialized")
print(f"Loaded configuration for {settings.APP_NAME} ({settings.ENV})")