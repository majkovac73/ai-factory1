import json
from app.agents.base_agent import BaseAgent
from app.core.utils.json_sanitizer import JSONSanitizer


class PlanningAgent(BaseAgent):
    """
    Product Development: Planning Agent
    
    Creates product roadmaps, release plans, and feature prioritization.
    """

    def __init__(self, provider=None, model: str = None):
        super().__init__(provider, model)
        self.sanitizer = JSONSanitizer()

    def plan(self, product_vision: str, timeline: str = "12 months") -> dict:
        """
        Create a structured product roadmap.
        
        Args:
            product_vision: Long-term product vision and goals
            timeline: Planning horizon (e.g., "12 months", "3 years")
        
        Returns:
            Dict with phases, features, milestones, and timeline
        """

        prompt = f"""
You are a product roadmap strategist.

Create a detailed product roadmap based on the vision and timeline.

Product Vision:
{product_vision}

Timeline: {timeline}

Return ONLY valid JSON with this structure:
{{
  "vision_summary": "Concise restatement of the vision",
  "phases": [
    {{
      "phase": "Phase 1: Foundation",
      "duration": "Q1-Q2 2024",
      "goals": ["goal1", "goal2"],
      "features": ["feature1", "feature2"],
      "deliverables": ["deliverable1"]
    }}
  ],
  "key_milestones": ["milestone1", "milestone2"],
  "success_metrics": ["metric1", "metric2"],
  "risks": ["risk1"],
  "dependencies": ["dependency1"]
}}

Be realistic and well-structured.
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
                    "vision_summary": response,
                    "phases": [],
                    "key_milestones": [],
                    "success_metrics": [],
                    "risks": [],
                    "dependencies": []
                }

    def run(self, task: dict) -> dict:
        """
        Standardized entry point. Expects a task dict with 'product_vision'
        and optional 'timeline' keys.
        """
        product_vision = task.get("product_vision", "")
        timeline = task.get("timeline", "12 months")
        return self.plan(product_vision, timeline)