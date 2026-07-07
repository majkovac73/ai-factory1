from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.services.analytics_service import AnalyticsService
from app.services.revenue_service import RevenueService
from app.services.performance_service import PerformanceService
from app.services.best_products_service import BestProductsService
from app.schemas.revenue import SaleCreate

router = APIRouter()
analytics_service = AnalyticsService()
revenue_service = RevenueService()
performance_service = PerformanceService()
best_products_service = BestProductsService()


@router.get("/events")
def list_events(
    event_type: Optional[str] = Query(default=None),
    entity_type: Optional[str] = Query(default=None),
    entity_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
):
    events = analytics_service.get_events(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        limit=limit,
    )
    return [
        {
            "id": e.id,
            "event_type": e.event_type,
            "entity_type": e.entity_type,
            "entity_id": e.entity_id,
            "value": e.value,
            "payload": e.payload,
            "created_at": e.created_at,
        }
        for e in events
    ]


@router.get("/summary")
def analytics_summary():
    return {
        "event_counts": analytics_service.get_event_counts_by_type(),
    }


@router.post("/revenue")
def record_sale(sale: SaleCreate):
    """
    Manually records a sale against a task/product. Etsy revenue isn't
    pulled automatically (this app only creates draft listings and has
    no transactions_r scope — see README), so the shop owner logs sales
    here after checking Etsy's own Shop Manager sales dashboard.
    """
    try:
        return revenue_service.record_sale(
            task_id=sale.task_id,
            amount=sale.amount,
            currency=sale.currency,
            quantity=sale.quantity,
            notes=sale.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/revenue/summary")
def revenue_summary(task_id: Optional[str] = Query(default=None)):
    return revenue_service.get_total_revenue(task_id=task_id)


@router.get("/revenue/by-task")
def revenue_by_task():
    return revenue_service.get_revenue_by_task()

@router.get("/performance/{task_id}")
def get_task_performance(task_id: str):
    try:
        return performance_service.score_task(task_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/performance")
def get_all_performance_scores():
    return performance_service.score_all_tasks()

@router.get("/best-products")
def get_best_products(limit: int = Query(default=10, ge=1, le=100), min_score: float = Query(default=None)):
    return best_products_service.get_best_products(limit=limit, min_score=min_score)


@router.get("/best-products/insights")
def get_best_product_insights(limit: int = Query(default=10, ge=1, le=100), min_score: float = Query(default=None)):
    return best_products_service.get_best_product_insights(limit=limit, min_score=min_score)