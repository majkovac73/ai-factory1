from app.agents.base_agent import BaseAgent


class CopywriterAgent(BaseAgent):
    """
    Creative: Copywriter Agent
    
    Generates high-converting marketing copy, product descriptions, and promotional content.
    """

    def write_copy(self, product_description: str, audience: str = "general", tone: str = "persuasive") -> str:
        """
        Write marketing copy for a product.
        
        Args:
            product_description: What the product is/does
            audience: Target audience (e.g., "busy professionals", "eco-conscious mothers")
            tone: Copy tone (e.g., "persuasive", "playful", "professional", "urgent")
        
        Returns:
            Marketing copy as a string
        """

        prompt = f"""
You are an expert marketing copywriter specializing in high-conversion sales copy.

Write compelling marketing copy for the following product.

Product Description:
{product_description}

Target Audience: {audience}
Tone: {tone}

Provide:
- Compelling headline (5-10 words)
- Hook paragraph (why they should care)
- 3-4 benefit bullets
- Objection handling
- Clear call-to-action

Make it engaging, specific, and conversion-focused. Avoid generic language.
Use power words and emotional triggers appropriate to the audience.
"""

        return self._generate(prompt)

    def run(self, task: dict) -> str:
        """
        Standardized entry point. Expects a task dict with 'product_description',
        optional 'audience' and 'tone' keys.
        """
        product_description = task.get("product_description", "")
        audience = task.get("audience", "general")
        tone = task.get("tone", "persuasive")
        return self.write_copy(product_description, audience, tone)