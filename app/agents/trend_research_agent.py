"""
TrendResearchAgent — step 88.

Thin orchestrator used by AutonomyWorker. Calls ResearchAgent + IntelligenceAgent
to surface one product opportunity for the autonomous task pipeline.

Returns a dict with at least a 'concept' key, or None if nothing promising found.
"""
import logging

from app.agents.market_intelligence.research import ResearchAgent
from app.agents.market_intelligence.intelligence import IntelligenceAgent

logger = logging.getLogger("ai-factory")

_RESEARCH_TOPIC = "Etsy digital download and print-on-demand trending products"
_RESEARCH_SCOPE = "trends"


class TrendResearchAgent:
    def __init__(self):
        self._research = ResearchAgent()
        self._intelligence = IntelligenceAgent()

    def run(self) -> dict | None:
        """
        Run a market research + synthesis cycle.
        Returns {'concept': str, 'opportunity': str} or None.
        """
        try:
            research = self._research.research(_RESEARCH_TOPIC, _RESEARCH_SCOPE)
        except Exception as e:
            logger.error(f"TrendResearchAgent: research step failed: {e}")
            return None

        try:
            intel = self._intelligence.synthesize(research, "")
        except Exception as e:
            logger.error(f"TrendResearchAgent: intelligence step failed: {e}")
            return None

        opportunities = intel.get("opportunities", [])
        if not opportunities:
            logger.info("TrendResearchAgent: no opportunities returned by intelligence agent")
            return None

        best = opportunities[0]
        return {
            "concept": best,
            "opportunity": best,
            "confidence": intel.get("confidence", "low"),
        }
