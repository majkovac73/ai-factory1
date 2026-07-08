import logging
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

load_dotenv()

from app.api.api import api_router
from app.db.database import Base, engine
from app.db.migrations import run_all_migrations
from app.models import agent_execution, log, task, task_step, etsy_token, marketing_post, pinterest_token, tumblr_token, analytics_event, image_asset, pod_product, fulfillment_record  # noqa: F401
import app.core.providers.openrouter_image_provider  # noqa: F401 — triggers provider self-registration
from app.workers.task_worker import TaskWorker
from app.workers.etsy_receipt_worker import EtsyReceiptWorker
from app.workers.autonomy_worker import AutonomyWorker
from config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai-factory")

run_all_migrations(engine)
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
    from app.core.providers.image_manager import ImageProviderManager
    logger.info(f"Image providers registered: {list(ImageProviderManager._registry.keys())}")

    from app.services.task_service import TaskService
    recovery_results = TaskService().recover_orphaned_tasks()
    if recovery_results["recovered"] or recovery_results["failed_permanently"]:
        logger.warning(f"AI Factory: startup recovery ran — {recovery_results}")
    else:
        logger.info("AI Factory: startup recovery found no orphaned tasks")

    task_worker.start()
    etsy_receipt_worker.start()
    autonomy_worker.start()


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("AI Factory server shutting down...")
    task_worker.stop()
    etsy_receipt_worker.stop()
    autonomy_worker.stop()


app.include_router(api_router)

_frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
app.mount("/ui", StaticFiles(directory=_frontend_dir, html=True), name="ui")

task_worker = TaskWorker()
etsy_receipt_worker = EtsyReceiptWorker()
autonomy_worker = AutonomyWorker()

logger.info("AI Factory API initialized")
print(f"Loaded configuration for {settings.APP_NAME} ({settings.ENV})")