from app.agents.base_agent import BaseAgent


class ResearchAgent(BaseAgent):
    """
    Market Intelligence: Research Agent
    
    Gathers competitive and market research data on a given topic.
    """

    def research(self, topic: str, scope: str = "general") -> str:
        """
        Research a market topic and return findings.
        
        Args:
            topic: What to research (e.g., "Etsy planner market")
            scope: Research scope (e.g., "competitors", "trends", "pricing")
        
        Returns:
            Research findings as a string
        """

        prompt = f"""
You are a market research analyst.

Research the following topic and provide comprehensive findings.

Topic: {topic}
Scope: {scope}

Provide:
- Key findings
- Notable competitors or trends
- Market size estimates (if available)
- Growth trajectory
- Potential opportunities

Be factual and data-driven. If you don't know something, say so.
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