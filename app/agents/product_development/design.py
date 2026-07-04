from app.agents.base_agent import BaseAgent


class DesignAgent(BaseAgent):
    """
    Product Development: Design Agent
    
    Creates product specifications, feature designs, and architectural blueprints.
    """

    def design(self, product_brief: str, constraints: str = "none") -> str:
        """
        Design a product based on a brief and constraints.
        
        Args:
            product_brief: High-level product description
            constraints: Technical, budget, or timeline constraints
        
        Returns:
            Product design specification as a string
        """

        prompt = f"""
You are a product designer and architect.

Design a product based on the following brief and constraints.

Product Brief:
{product_brief}

Constraints:
{constraints}

Provide a detailed design specification covering:
- Core features and functionality
- User experience flow
- Technical architecture overview
- Component breakdown
- Success metrics
- Estimated timeline and resources

Be practical and specific.
"""

        return self._generate(prompt)

    def run(self, task: dict) -> str:
        """
        Standardized entry point. Expects a task dict with 'product_brief'
        and optional 'constraints' keys.
        """
        product_brief = task.get("product_brief", "")
        constraints = task.get("constraints", "none")
        return self.design(product_brief, constraints)