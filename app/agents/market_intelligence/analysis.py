from app.agents.base_agent import BaseAgent


class AnalysisAgent(BaseAgent):
    """
    Market Intelligence: Analysis Agent
    
    Analyzes market research data and identifies patterns, opportunities,
    and strategic insights.
    """

    def analyze(self, research_data: str, focus: str = "opportunities") -> str:
        """
        Analyze research data and synthesize insights.
        
        Args:
            research_data: Raw research findings to analyze
            focus: What to focus on (e.g., "opportunities", "threats", "trends")
        
        Returns:
            Analysis and insights as a string
        """

        prompt = f"""
You are a strategic market analyst.

Analyze the following research data and synthesize insights.

Research Data:
{research_data}

Focus: {focus}

Provide:
- Key patterns and trends
- Strategic opportunities
- Risks and challenges
- Recommendations for action
- Competitive positioning advice

Be analytical and forward-thinking.
"""

        return self._generate(prompt)

    def run(self, task: dict) -> str:
        """
        Standardized entry point. Expects a task dict with 'research_data'
        and optional 'focus' keys.
        """
        research_data = task.get("research_data", "")
        focus = task.get("focus", "opportunities")
        return self.analyze(research_data, focus)