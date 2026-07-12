"""
Market Intelligence Agents — agents for research, analysis, and competitive intelligence.
To add a new agent here, create a class inheriting from BaseAgent and export it in this __init__.
"""

from app.agents.market_intelligence.research import ResearchAgent
from app.agents.market_intelligence.intelligence import IntelligenceAgent

# 2-3: AnalysisAgent was dead code (the loop always passed an empty analysis) — removed.
__all__ = ["ResearchAgent", "IntelligenceAgent"]