import logging
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from app.api.api import api_router
from app.api.auth import is_authorized
from app.db.database import Base, engine
from app.db.migrations import run_all_migrations
from app.models import agent_execution, log, task, task_step, etsy_token, marketing_post, pinterest_token, tumblr_token, analytics_event, image_asset, pod_product, fulfillment_record  # noqa: F401
import app.core.providers.openrouter_image_provider  # noqa: F401 — triggers provider self-registration
from app.workers.task_worker import TaskWorker
from app.workers.etsy_receipt_worker import EtsyReceiptWorker
from app.workers.autonomy_worker import AutonomyWorker
from app.workers.marketing_refresh_worker import MarketingRefreshWorker
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

@app.middleware("http")
async def factory_key_guard(request: Request, call_next):
    """
    P0-3: gate money-spending / shop-mutating requests behind FACTORY_API_KEY.

    Enforcement is OFF when FACTORY_API_KEY is unset (deploy-safe: nothing
    breaks). When set, any POST/PUT/PATCH/DELETE — plus the sensitive /logs
    reads (they contain full prompts/outputs) — must carry header
    `X-Factory-Key: <FACTORY_API_KEY>`. Read-only dashboards, /health (Railway
    healthcheck), the /ui static frontend, and external OAuth callbacks stay
    open so the browser dashboard and platform healthcheck keep working.
    See app/api/auth.py for the (unit-tested) decision logic.
    """
    if not is_authorized(
        request.url.path, request.method, request.headers.get("X-Factory-Key")
    ):
        return JSONResponse(
            status_code=401,
            content={"detail": "Missing or invalid X-Factory-Key"},
        )
    return await call_next(request)


def _resume_incomplete_pipelines():
    """P0-9: re-run run_post_completion for DONE tasks that crashed mid-pipeline
    (no recorded pipeline_status). Bounded by PIPELINE_RESUME_WINDOW_HOURS/MAX so
    it can't mass-regenerate history. Runs in a daemon thread off startup."""
    try:
        from app.services.task_service import TaskService
        from app.services.pipeline_orchestrator import PipelineOrchestrator
        from app.core.product_formats import PRODUCT_FORMATS

        candidates = TaskService().get_resumable_pipeline_tasks(
            window_hours=settings.PIPELINE_RESUME_WINDOW_HOURS,
            limit=settings.PIPELINE_RESUME_MAX,
        )
        if not candidates:
            logger.info("PipelineResume: no incomplete pipelines to resume")
            return
        orch = PipelineOrchestrator()
        for task_id, task_type in candidates:
            if task_type not in PRODUCT_FORMATS:
                continue
            logger.warning(f"PipelineResume: resuming post-completion pipeline for task {task_id}")
            try:
                orch.run_post_completion(task_id)
            except Exception as e:
                logger.error(f"PipelineResume: resume failed for {task_id}: {e}")
    except Exception as e:
        logger.error(f"PipelineResume: scan failed: {e}")


@app.on_event("startup")
async def startup_event():
    logger.info("AI Factory server starting...")
    from app.core.providers.image_manager import ImageProviderManager
    logger.info(f"Image providers registered: {list(ImageProviderManager._registry.keys())}")

    from app.services.task_service import TaskService
    task_service = TaskService()
    recovery_results = task_service.recover_orphaned_tasks()
    if recovery_results["recovered"] or recovery_results["failed_permanently"]:
        logger.warning(f"AI Factory: startup recovery ran — {recovery_results}")
    else:
        logger.info("AI Factory: startup recovery found no orphaned tasks")

    # P0-9: the TaskQueue is in-memory — re-enqueue tasks stranded in NEW at
    # crash time, and resume tasks that crashed mid post-completion pipeline
    # (bounded, in a background thread so the healthcheck isn't delayed).
    try:
        task_service.enqueue_new_tasks()
    except Exception as e:
        logger.error(f"AI Factory: failed to re-enqueue NEW tasks: {e}")
    import threading
    threading.Thread(target=_resume_incomplete_pipelines, daemon=True, name="PipelineResume").start()

    task_worker.start()
    etsy_receipt_worker.start()
    autonomy_worker.start()
    marketing_refresh_worker.start()


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("AI Factory server shutting down...")
    task_worker.stop()
    etsy_receipt_worker.stop()
    autonomy_worker.stop()
    marketing_refresh_worker.stop()


app.include_router(api_router)

_frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
app.mount("/ui", StaticFiles(directory=_frontend_dir, html=True), name="ui")

task_worker = TaskWorker()
etsy_receipt_worker = EtsyReceiptWorker()
autonomy_worker = AutonomyWorker()
marketing_refresh_worker = MarketingRefreshWorker()

logger.info("AI Factory API initialized")
print(f"Loaded configuration for {settings.APP_NAME} ({settings.ENV})")