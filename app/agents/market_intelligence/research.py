from app.agents.base_agent import BaseAgent


class ResearchAgent(BaseAgent):
    """
    Market Intelligence: Research Agent
    
    Gathers competitive and market research data on a given topic.
    """

    def research(self, topic: str, scope: str = "general", real_trend_data: dict = None) -> str:
        """
        Research a market topic and return findings, grounded in real
        trend data rather than model imagination.

        Args:
            topic: What to research (e.g., "Etsy planner market")
            scope: Research scope (e.g., "competitors", "trends", "pricing")
            real_trend_data: Output of TrendDataService.get_real_trend_signals().
                REQUIRED for meaningful output — if omitted, the model is
                explicitly told it has no real data and must say so rather
                than invent findings.

        Returns:
            Research findings as a string
        """
        if real_trend_data:
            data_block = (
                "Real Google Trends data collected this cycle (rising "
                "search queries and recent interest levels):\n"
                f"{real_trend_data}"
            )
        else:
            data_block = (
                "NO real trend data was available this cycle. Do not "
                "invent or assume any market findings. State plainly that "
                "no real data was available."
            )

        prompt = f"""
You are a market research analyst.

Topic: {topic}
Scope: {scope}

{data_block}

Using ONLY the real data above (if present), provide:
- Key findings grounded specifically in the rising queries / interest
  levels shown above — reference the actual keywords and numbers
- Notable patterns across the keywords
- Which specific keywords show the strongest real signal
- Potential opportunities tied directly to specific rising queries

Do not state a finding unless it is directly traceable to the real data
provided. If the real data is thin or absent, say so explicitly instead of
filling the gap with assumptions.
"""

        return self._generate(prompt)

    def run(self, task: dict) -> str:
        """
        Standardized entry point. Expects a task dict with 'topic' and
        optional 'scope' keys.
        """
        topic = task.get("topic", "")
        scope = task.get("scope", "general")
        return self.research(topic, scope)