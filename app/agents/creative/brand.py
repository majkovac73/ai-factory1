import json
from app.agents.base_agent import BaseAgent
from app.core.utils.json_sanitizer import JSONSanitizer


class BrandAgent(BaseAgent):
    """
    Creative: Brand Agent
    
    Develops brand strategy, positioning, messaging frameworks, and voice guidelines.
    """

    def __init__(self, provider=None, model: str = None):
        super().__init__(provider, model)
        self.sanitizer = JSONSanitizer()

    def develop_brand(self, company_name: str, mission: str, market: str) -> dict:
        """
        Develop a comprehensive brand strategy.
        
        Args:
            company_name: Name of the business/brand
            mission: What the company does / why it exists
            market: Target market (e.g., "eco-conscious consumers", "indie creators")
        
        Returns:
            Dict with brand positioning, voice, values, messaging
        """

        prompt = f"""
You are a brand strategist.

Develop a comprehensive brand strategy for the following company.

Company: {company_name}
Mission: {mission}
Target Market: {market}

Return ONLY valid JSON with this structure:
{{
  "brand_positioning": "One-sentence unique positioning statement",
  "brand_promise": "What customers get from this brand",
  "core_values": ["value1", "value2", "value3"],
  "brand_personality": {{
    "tone": "e.g., friendly, professional, irreverent",
    "style": "e.g., minimalist, bold, playful",
    "voice": "Key characteristics of brand voice"
  }},
  "messaging_pillars": [
    {{
      "pillar": "e.g., Quality",
      "message": "Our commitment to quality means..."
    }}
  ],
  "key_differentiators": ["differentiator1", "differentiator2"],
  "brand_colors": ["#HEX1", "#HEX2"],
  "typography_guidance": "Recommended font styles/sizes"
}}

Be specific and actionable.
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
                    "brand_positioning": mission,
                    "brand_promise": f"Serving {market}",
                    "core_values": ["quality", "integrity", "innovation"],
                    "brand_personality": {"tone": "professional", "style": "modern", "voice": response},
                    "messaging_pillars": [],
                    "key_differentiators": [],
                    "brand_colors": ["#000000", "#FFFFFF"],
                    "typography_guidance": "Clean, modern sans-serif"
                }

    def run(self, task: dict) -> dict:
        """
        Standardized entry point. Expects a task dict with 'company_name',
        'mission', and 'market' keys.
        """
        company_name = task.get("company_name", "")
        mission = task.get("mission", "")
        market = task.get("market", "")
        return self.develop_brand(company_name, mission, market)