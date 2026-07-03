import logging

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from app.api.api import api_router
from app.db.database import Base, engine
from app.models import agent_execution, log, task, task_step  # noqa: F401
from config import settings

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

@app.on_event("startup")
async def startup_event():
    logger.info("AI Factory server starting...")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("AI Factory server shutting down...")


app.include_router(api_router)

logger.info("AI Factory API initialized")
print(f"Loaded configuration for {settings.APP_NAME} ({settings.ENV})")