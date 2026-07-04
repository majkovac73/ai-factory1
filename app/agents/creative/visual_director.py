from app.agents.base_agent import BaseAgent


class VisualDirectorAgent(BaseAgent):
    """
    Creative: Visual Director Agent
    
    Creates visual design briefs, aesthetic direction, and design system guidance.
    (Does not generate actual images — provides direction for image generation or designer.)
    """

    def direct_visual_design(self, product_name: str, brand_context: str, use_case: str = "product listing") -> str:
        """
        Create a visual design brief and direction.
        
        Args:
            product_name: Name/type of product to design visuals for
            brand_context: Brand voice, colors, personality
            use_case: Where the image will be used (e.g., "product listing", "social media", "print")
        
        Returns:
            Visual design brief as a string
        """

        prompt = f"""
You are a visual design director.

Create a detailed visual design brief for the following.

Product: {product_name}
Brand Context: {brand_context}
Use Case: {use_case}

Provide:
- Visual style (e.g., "minimalist", "bold", "whimsical")
- Color palette (specific hex codes or descriptions)
- Composition approach (e.g., "lifestyle shot", "flat lay", "hero product")
- Mood and emotional tone
- Key visual elements to include
- Elements to avoid
- Typography style if text is involved
- Recommended image dimensions
- Background treatment
- Props or context to include

Be specific enough that an AI image generator or designer can execute this exactly.
Avoid vague terms; use concrete visual language.
"""

        return self._generate(prompt)

    def run(self, task: dict) -> str:
        """
        Standardized entry point. Expects a task dict with 'product_name',
        'brand_context', and optional 'use_case' keys.
        """
        product_name = task.get("product_name", "")
        brand_context = task.get("brand_context", "")
        use_case = task.get("use_case", "product listing")
        return self.direct_visual_design(product_name, brand_context, use_case)