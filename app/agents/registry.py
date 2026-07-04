"""
Agent Registry — centralized mapping of agent names to their classes.
Makes it easy to add new agents without hardcoding imports everywhere.

Agents are lazy-loaded to avoid circular imports.
"""


def _get_registry():
    """
    Lazy-load the agent registry. Agents are imported here (not at module level)
    to avoid circular import issues where agents import BaseAgent which is in
    this same package tree.
    """
    from app.core.agents.planner import PlannerAgent
    from app.core.agents.executor import ExecutorAgent
    from app.core.agents.generator import GeneratorAgent
    from app.core.agents.critic import CriticAgent
    from app.core.agents.fixer import FixerAgent
    from app.core.agents.qa import QAAgent
    from app.agents.market_intelligence.research import ResearchAgent
    from app.agents.market_intelligence.analysis import AnalysisAgent
    from app.agents.market_intelligence.intelligence import IntelligenceAgent
    from app.agents.product_development.design import DesignAgent
    from app.agents.product_development.optimization import OptimizationAgent
    from app.agents.product_development.planning import PlanningAgent
    from app.agents.creative.copywriter import CopywriterAgent
    from app.agents.creative.brand import BrandAgent
    from app.agents.creative.visual_director import VisualDirectorAgent
    from app.agents.qa.consistency import ConsistencyAgent
    from app.agents.qa.fact_check import FactCheckAgent
    from app.agents.qa.completeness import CompletenessAgent
    from app.agents.etsy.product_generator import ProductGeneratorAgent
    from app.agents.etsy.seo_generator import SEOGeneratorAgent
    from app.agents.etsy.listing_generator import ListingGeneratorAgent

    return {
        "planner": PlannerAgent,
        "executor": ExecutorAgent,
        "generator": GeneratorAgent,
        "critic": CriticAgent,
        "fixer": FixerAgent,
        "qa": QAAgent,
        "research": ResearchAgent,
        "analysis": AnalysisAgent,
        "intelligence": IntelligenceAgent,
        "design": DesignAgent,
        "optimization": OptimizationAgent,
        "planning": PlanningAgent,
        "copywriter": CopywriterAgent,
        "brand": BrandAgent,
        "visual_director": VisualDirectorAgent,
        "consistency": ConsistencyAgent,
        "fact_check": FactCheckAgent,
        "completeness": CompletenessAgent,
        "product_generator": ProductGeneratorAgent,
        "seo_generator": SEOGeneratorAgent,
        "listing_generator": ListingGeneratorAgent,
    }


# Cached registry (lazily initialized on first use)
_CACHED_REGISTRY = None


def get_agent(agent_name: str, **kwargs):
    """
    Instantiate an agent by name. Kwargs are passed to the agent's constructor.
    
    Args:
        agent_name: Key in the agent registry (e.g., "planner", "critic")
        **kwargs: Constructor arguments (provider, model, memory, etc.)
    
    Returns:
        Agent instance
        
    Raises:
        ValueError if agent_name not found in registry
    """
    global _CACHED_REGISTRY
    if _CACHED_REGISTRY is None:
        _CACHED_REGISTRY = _get_registry()
    
    if agent_name not in _CACHED_REGISTRY:
        raise ValueError(
            f"Unknown agent '{agent_name}'. "
            f"Available agents: {', '.join(sorted(_CACHED_REGISTRY.keys()))}"
        )
    
    agent_class = _CACHED_REGISTRY[agent_name]
    return agent_class(**kwargs)


def list_agents():
    """Return a list of all registered agent names."""
    global _CACHED_REGISTRY
    if _CACHED_REGISTRY is None:
        _CACHED_REGISTRY = _get_registry()
    return sorted(_CACHED_REGISTRY.keys())


def AGENT_REGISTRY():
    """Return the full agent registry dict. Lazy-loads on first call."""
    global _CACHED_REGISTRY
    if _CACHED_REGISTRY is None:
        _CACHED_REGISTRY = _get_registry()
    return _CACHED_REGISTRY