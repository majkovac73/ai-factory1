import json
from app.agents.base_agent import BaseAgent
from app.core.utils.json_sanitizer import JSONSanitizer


class IntelligenceAgent(BaseAgent):
    """
    Market Intelligence: Intelligence Agent

    Synthesizes market research into actionable intelligence reports.

    2-3: the old AnalysisAgent was dead code — the loop always called
    synthesize(research, "") with an empty analysis. That agent + parameter are
    gone; synthesize takes research only.
    """

    def __init__(self, provider=None, model: str = None):
        super().__init__(provider, model)
        self.sanitizer = JSONSanitizer()

    def synthesize(self, research: str) -> dict:
        """
        Synthesize research into a structured intelligence report.

        Args:
            research: Research findings

        Returns:
            Dict with keys: summary, opportunities, threats, recommendations
        """

        prompt = f"""
You are a market intelligence director.

Synthesize the following research into a concise intelligence report.

Research:
{research}

Return ONLY valid JSON with this structure:
{{
  "summary": "Executive summary of market findings",
  "opportunities": ["opportunity1", "opportunity2", "opportunity3"],
  "threats": ["threat1", "threat2"],
  "recommendations": ["recommendation1", "recommendation2"],
  "confidence": "high/medium/low"
}}

Be concise and actionable.
"""

        response = self._generate(prompt)

        try:
            return json.loads(response)
        except Exception:
            try:
                parsed = self.sanitizer.extract(response)
                return parsed
            except Exception:
                return {
                    "summary": response,
                    "opportunities": [],
                    "threats": [],
                    "recommendations": [],
                    "confidence": "low"
                }

    def run(self, task: dict) -> dict:
        """
        Standardized entry point. Expects a task dict with a 'research' key.
        """
        research = task.get("research", "")
        return self.synthesize(research)