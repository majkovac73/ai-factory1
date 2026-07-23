"""
BrainService — the factory's brain.

The app already CAPTURES nearly everything: every action lands in the `logs`
table, every meaningful event (concept scored, sale, listing views, trend
signal, niche verdict…) in `analytics_events`, every product with full provenance
in `tasks`/`task_steps`. What was missing is ORGANIZATION + USE: a place that
distills all that raw history into decision-ready knowledge and hands it back to
the factory when it decides what to build.

This service is that brain. It:
  * stores organized knowledge (observation / decision / lesson / research /
    outcome) in the `knowledge` table — lessons EVOLVE per subject, they don't
    pile up as duplicates;
  * consolidate() distills the existing streams (niche memory, revenue, scoring,
    traffic, production) into lessons — deterministic, NO LLM cost — and runs on
    its own from the daily worker tick;
  * context_block() feeds the sharpest lessons into concept generation, so the
    factory literally decides using what it has learned;
  * summary()/recall() expose the brain for the dashboard and any consumer.
"""
import logging
import uuid
from datetime import datetime, timedelta

logger = logging.getLogger("ai-factory")

KINDS = ("observation", "decision", "lesson", "research", "outcome")
# lessons + outcomes are DURABLE (upserted per subject so they evolve);
# observations/decisions/research are a TIMELINE (appended, then pruned).
_UPSERT_KINDS = ("lesson", "outcome")


class BrainService:
    # ── write ────────────────────────────────────────────────────────────────
    def remember(self, kind: str, category: str, subject: str, content: str,
                 data: dict = None, confidence: float = 0.5, source: str = "") -> str:
        """Record a piece of knowledge. Durable kinds (lesson/outcome) upsert by
        (kind, category, subject) so the brain's understanding of a topic evolves;
        timeline kinds append. Best-effort — never raises into the caller."""
        try:
            from app.db.database import SessionLocal
            from app.models.knowledge import Knowledge
            db = SessionLocal()
            try:
                row = None
                if kind in _UPSERT_KINDS:
                    row = (db.query(Knowledge)
                           .filter(Knowledge.kind == kind, Knowledge.category == category,
                                   Knowledge.subject == subject)
                           .first())
                if row:
                    row.content = content
                    row.data = data or {}
                    row.confidence = float(confidence)
                    row.source = source
                    row.updated_at = datetime.utcnow()
                    kid = row.id
                else:
                    kid = str(uuid.uuid4())
                    db.add(Knowledge(id=kid, kind=kind, category=category, subject=subject[:200],
                                     content=content[:4000], data=data or {},
                                     confidence=float(confidence), source=source))
                db.commit()
                return kid
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"BrainService: remember failed ({kind}/{category}/{subject}): {e}")
            return ""

    def observe(self, category, subject, content, data=None, source=""):
        return self.remember("observation", category, subject, content, data, 0.6, source)

    def decide(self, category, subject, content, data=None, source=""):
        return self.remember("decision", category, subject, content, data, 0.7, source)

    def learn(self, category, subject, content, data=None, confidence=0.7, source=""):
        return self.remember("lesson", category, subject, content, data, confidence, source)

    def research(self, category, subject, content, data=None, source=""):
        return self.remember("research", category, subject, content, data, 0.5, source)

    def record_outcome(self, category, subject, content, data=None, confidence=0.8, source=""):
        return self.remember("outcome", category, subject, content, data, confidence, source)

    # ── read ─────────────────────────────────────────────────────────────────
    def recall(self, kind: str = None, category: str = None, limit: int = 50) -> list:
        try:
            from app.db.database import SessionLocal
            from app.models.knowledge import Knowledge
            db = SessionLocal()
            try:
                q = db.query(Knowledge)
                if kind:
                    q = q.filter(Knowledge.kind == kind)
                if category:
                    q = q.filter(Knowledge.category == category)
                rows = q.order_by(Knowledge.updated_at.desc()).limit(limit).all()
                return [self._fmt(r) for r in rows]
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"BrainService: recall failed: {e}")
            return []

    @staticmethod
    def _fmt(r) -> dict:
        return {"kind": r.kind, "category": r.category, "subject": r.subject,
                "content": r.content, "confidence": round(r.confidence, 2),
                "data": r.data or {}, "source": r.source,
                "at": r.updated_at.isoformat() if r.updated_at else None}

    def summary(self) -> dict:
        """A dashboard-friendly view of what the brain knows."""
        try:
            from app.db.database import SessionLocal
            from app.models.knowledge import Knowledge
            from sqlalchemy import func
            db = SessionLocal()
            try:
                total = db.query(Knowledge).count()
                by_kind = dict(db.query(Knowledge.kind, func.count(Knowledge.id)).group_by(Knowledge.kind).all())
                by_cat = dict(db.query(Knowledge.category, func.count(Knowledge.id)).group_by(Knowledge.category).all())
                lessons = [self._fmt(r) for r in db.query(Knowledge)
                           .filter(Knowledge.kind == "lesson")
                           .order_by(Knowledge.confidence.desc(), Knowledge.updated_at.desc())
                           .limit(15).all()]
                recent = [self._fmt(r) for r in db.query(Knowledge)
                          .order_by(Knowledge.updated_at.desc()).limit(15).all()]
                return {"total": total, "by_kind": by_kind, "by_category": by_cat,
                        "top_lessons": lessons, "recent": recent}
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"BrainService: summary failed: {e}")
            return {"total": 0, "by_kind": {}, "by_category": {}, "top_lessons": [], "recent": []}

    def context_block(self, max_lessons: int = 8) -> str:
        """The knowledge the factory consults when deciding what to build — the
        highest-confidence lessons, concise. Empty until the brain has learned
        something, so it never injects noise."""
        try:
            from app.db.database import SessionLocal
            from app.models.knowledge import Knowledge
            db = SessionLocal()
            try:
                rows = (db.query(Knowledge)
                        .filter(Knowledge.kind == "lesson", Knowledge.confidence >= 0.6)
                        .order_by(Knowledge.confidence.desc(), Knowledge.updated_at.desc())
                        .limit(max_lessons).all())
            finally:
                db.close()
            if not rows:
                return ""
            lines = "\n".join(f"- [{r.category}] {r.content}" for r in rows)
            return ("\n\nWHAT THE FACTORY'S BRAIN HAS LEARNED (apply these lessons — they come "
                    "from THIS shop's real history):\n" + lines)
        except Exception:
            return ""

    # ── autonomous consolidation ──────────────────────────────────────────────
    def consolidate(self) -> dict:
        """Distill the app's raw history into organized lessons. Deterministic and
        LLM-free. Runs on its own from the daily worker tick. Returns a small
        summary of what it learned this pass."""
        learned = 0
        try:
            from config import settings
            from app.services.analytics_service import AnalyticsService
            from app.services.revenue_service import RevenueService
            an = AnalyticsService()
            rev = RevenueService()

            # 1) NICHES — from the niche memory verdicts
            try:
                from app.services.niche_memory_service import NicheMemoryService
                mem = NicheMemoryService().load()
                if mem.get("signal_trustworthy"):
                    for key, v in (mem.get("themes") or {}).items():
                        verd = v.get("verdict")
                        if verd not in ("winner", "loser"):
                            continue
                        name = key.split(":", 1)[-1]
                        if verd == "winner":
                            c = (f"Niche '{name}' WORKS — {'€%.0f earned, ' % v['revenue'] if v.get('revenue') else ''}"
                                 f"{v.get('views',0)} views over {v.get('n_listings')} listings. Make more here.")
                            conf = 0.85 if v.get("revenue") else 0.7
                        else:
                            c = (f"Niche '{name}' is a DEAD END — {v.get('n_listings')} listings, "
                                 f"~{v.get('avg_views',0)} views each, no sales. Stop making these.")
                            conf = 0.7
                        self.learn("niche", name, c, data=v, confidence=conf, source="niche_memory")
                        learned += 1
            except Exception as e:
                logger.warning(f"BrainService.consolidate niche step: {e}")

            # 2) FINANCE — which formats earn (or don't)
            try:
                pbf = rev.profit_by_format() or {}
                earners = sorted(((f, a) for f, a in pbf.items() if a.get("sales")),
                                 key=lambda kv: kv[1]["net"], reverse=True)
                for fmt, a in earners[:5]:
                    self.learn("finance", fmt,
                               f"Format '{fmt}' earns: €{a['net']:.2f} net from {a['sales']} sale(s) "
                               f"(avg €{a.get('avg_price',0):.2f}). Prioritize it.",
                               data=a, confidence=0.85, source="revenue")
                    learned += 1
            except Exception as e:
                logger.warning(f"BrainService.consolidate finance step: {e}")

            # 3) MARKET — traffic reality (are we getting views/sales at all?)
            try:
                totrev = rev.get_total_revenue() or {}
                sales = int(totrev.get("sale_count", 0) or 0)
                stats = an.get_events(event_type="listing_stats", limit=2000)
                latest = {}
                for e in stats:  # newest first -> keep first per task
                    if e.entity_id not in latest:
                        latest[e.entity_id] = int((e.payload or {}).get("views", 0) or 0)
                total_views = sum(latest.values())
                floor = int(getattr(settings, "LEARNING_MIN_VIEWS_FOR_SIGNAL", 50))
                if sales > 0:
                    c = f"The shop has {sales} sale(s) and {total_views} tracked views — real demand exists; lean into what sold."
                    conf = 0.8
                elif total_views < floor:
                    c = (f"Traffic is the bottleneck: only {total_views} total listing views and 0 sales. "
                         "Product ideas are unproven — ground concepts in EXTERNAL demand + drive traffic, "
                         "don't trust internal 'popularity' yet.")
                    conf = 0.75
                else:
                    c = f"{total_views} views but 0 sales — traffic exists, conversion doesn't. Fix listings/pricing before scaling."
                    conf = 0.7
                self.learn("market", "traffic", c, data={"views": total_views, "sales": sales}, confidence=conf, source="analytics")
                learned += 1
            except Exception as e:
                logger.warning(f"BrainService.consolidate market step: {e}")

            # 4) QUALITY — concept scoring pass-rate (last 24h)
            try:
                since = datetime.utcnow() - timedelta(hours=24)
                scored = [e for e in an.get_events(event_type="concept_scored", limit=1000)
                          if e.created_at and e.created_at >= since]
                if scored:
                    passed = sum(1 for e in scored if (e.payload or {}).get("passed"))
                    rate = passed / len(scored)
                    c = (f"Concept quality gate: {passed}/{len(scored)} passed in 24h ({rate*100:.0f}%). "
                         + ("Healthy find rate." if rate >= 0.1 else
                            "LOW pass rate — the gate is starving the factory or concepts are weak; watch spend."))
                    self.learn("quality", "concept_pass_rate", c,
                               data={"passed": passed, "scored": len(scored)}, confidence=0.65, source="analytics")
                    learned += 1
            except Exception as e:
                logger.warning(f"BrainService.consolidate quality step: {e}")

            # 5) PRODUCTION — how much the factory has built
            try:
                from app.core.product_formats import PRODUCT_FORMATS
                from app.services.task_service import TaskService
                pubs = [t for t in TaskService().list_tasks()
                        if t.type in PRODUCT_FORMATS and (t.output_data or {}).get("listing_id")]
                self.observe("product", "catalog_size",
                             f"{len(pubs)} published listings in the shop.",
                             data={"count": len(pubs)}, source="tasks")
                learned += 1
            except Exception as e:
                logger.warning(f"BrainService.consolidate production step: {e}")

            self._prune()
            try:
                an.record_event(event_type="brain_consolidated", entity_type="shop", entity_id="shop",
                                value=float(learned), payload={"lessons_touched": learned})
            except Exception:
                pass
            logger.info(f"BrainService: consolidated — {learned} knowledge item(s) updated")
            return {"updated": learned}
        except Exception as e:
            logger.warning(f"BrainService: consolidate failed: {e}")
            return {"updated": learned, "error": str(e)}

    def _prune(self, keep_days: int = 45, keep_max: int = 500):
        """Keep the timeline kinds from growing forever (lessons/outcomes are kept
        — they're the durable brain)."""
        try:
            from app.db.database import SessionLocal
            from app.models.knowledge import Knowledge
            cutoff = datetime.utcnow() - timedelta(days=keep_days)
            db = SessionLocal()
            try:
                (db.query(Knowledge)
                 .filter(Knowledge.kind.in_(["observation", "decision", "research"]),
                         Knowledge.created_at < cutoff)
                 .delete(synchronize_session=False))
                db.commit()
            finally:
                db.close()
        except Exception:
            pass
