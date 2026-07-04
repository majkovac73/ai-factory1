import json
from app.agents.base_agent import BaseAgent
from app.core.utils.json_sanitizer import JSONSanitizer


class IntelligenceAgent(BaseAgent):
    """
    Market Intelligence: Intelligence Agent
    
    Synthesizes market research and analysis into actionable intelligence reports.
    """

    def __init__(self, provider=None, model: str = None):
        super().__init__(provider, model)
        self.sanitizer = JSONSanitizer()

    def synthesize(self, research: str, analysis: str) -> dict:
        """
        Synthesize research and analysis into a structured intelligence report.
        
        Args:
            research: Research findings
            analysis: Strategic analysis
        
        Returns:
            Dict with keys: summary, opportunities, threats, recommendations
        """

        prompt = f"""
You are a market intelligence director.

Synthesize the following research and analysis into a concise intelligence report.

Research:
{research}

Analysis:
{analysis}

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
        Standardized entry point. Expects a task dict with 'research'
        and 'analysis' keys.
        """
        research = task.get("research", "")
        analysis = task.get("analysis", "")
        return self.synthesize(research, analysis)