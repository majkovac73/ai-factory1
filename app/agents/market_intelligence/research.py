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

        # 1-3: give the analyst a calendar so it reads TRAILING Google Trends data
        # with today's date in hand (July still shows graduation/Father's-Day
        # risers; January still shows Christmas — those are PAST, not opportunities).
        try:
            from app.core.seasonality import seasonal_prompt_block
            season_block = seasonal_prompt_block()
        except Exception:
            season_block = ""

        prompt = f"""
You are a market research analyst.

Topic: {topic}
Scope: {scope}
{season_block}

{data_block}

Note: Google Trends is a TRAILING window — a query can be a top riser because its
occasion just PASSED. Treat any occasion that has passed or is outside its
buying window (per the seasonal timing above) as NOT an opportunity.

The data includes an "interest_trend" per keyword with a "direction"
(rising/flat/falling). Weight RISING keywords far more heavily than falling ones
— a falling keyword's wave has already crested and a new listing arrives too late.

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