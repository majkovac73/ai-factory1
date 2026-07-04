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