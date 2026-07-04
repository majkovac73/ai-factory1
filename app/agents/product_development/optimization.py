from app.agents.base_agent import BaseAgent


class OptimizationAgent(BaseAgent):
    """
    Product Development: Optimization Agent
    
    Analyzes product performance and recommends optimizations for speed,
    cost, quality, and user experience.
    """

    def optimize(self, product_spec: str, focus: str = "performance") -> str:
        """
        Analyze a product and recommend optimizations.
        
        Args:
            product_spec: Current product specification or description
            focus: Optimization area (e.g., "performance", "cost", "quality", "ux")
        
        Returns:
            Optimization recommendations as a string
        """

        prompt = f"""
You are a product optimization specialist.

Analyze the following product and recommend optimizations.

Product Specification:
{product_spec}

Focus Area: {focus}

Provide:
- Current bottlenecks or inefficiencies
- Specific optimization recommendations
- Expected impact (performance gain, cost reduction, etc.)
- Implementation difficulty (low/medium/high)
- Risk assessment
- Priority ranking of recommendations

Be actionable and data-driven where possible.
"""

        return self._generate(prompt)

    def run(self, task: dict) -> str:
        """
        Standardized entry point. Expects a task dict with 'product_spec'
        and optional 'focus' keys.
        """
        product_spec = task.get("product_spec", "")
        focus = task.get("focus", "performance")
        return self.optimize(product_spec, focus)