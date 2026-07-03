from enum import Enum
from typing import Dict, Any, Optional


class Capability(str, Enum):
    """
    Defines what type of work a task belongs to.
    Used for routing tasks to correct agents.
    """
    PLANNING = "planning"
    EXECUTION = "execution"
    QA = "qa"
    RESEARCH = "research"
    CONTENT_GENERATION = "content_generation"
    AUTOMATION = "automation"
    UNKNOWN = "unknown"


class AIRouter:
    """
    AI Router decides which agent/pipeline stage should handle a task.
    This is a lightweight intelligence layer between Orchestrator and Agents.
    """

    def __init__(self):
        # keyword-based fallback mapping (fast routing without LLM)
        self.keyword_map = {
            Capability.RESEARCH: [
                "research", "analyze", "find", "investigate", "data", "compare"
            ],
            Capability.CONTENT_GENERATION: [
                "write", "create post", "generate text", "seo", "blog", "description"
            ],
            Capability.AUTOMATION: [
                "automate", "schedule", "post", "upload", "sync", "api"
            ],
            Capability.QA: [
                "check", "validate", "review", "fix", "debug"
            ],
            Capability.EXECUTION: [
                "run", "execute", "build", "implement", "code", "create system"
            ],
            Capability.PLANNING: [
                "plan", "design", "architecture", "strategy"
            ]
        }

    # -----------------------------
    # MAIN ROUTING FUNCTION
    # -----------------------------
    def route(self, task: Dict[str, Any]) -> Capability:
        """
        Main entry point.
        Input: task dict (must contain at least 'title' or 'description')
        Output: Capability enum
        """

        text = self._extract_text(task)

        # 1. Try keyword-based routing first (fast, deterministic)
        capability = self._keyword_route(text)
        if capability != Capability.UNKNOWN:
            return capability

        # 2. Fallback rule-based heuristic
        return self._heuristic_route(text)

    # -----------------------------
    # TEXT EXTRACTION
    # -----------------------------
    def _extract_text(self, task: Dict[str, Any]) -> str:
        return (
            task.get("title", "") + " " +
            task.get("description", "")
        ).lower()

    # -----------------------------
    # KEYWORD ROUTING
    # -----------------------------
    def _keyword_route(self, text: str) -> Capability:
        for capability, keywords in self.keyword_map.items():
            for kw in keywords:
                if kw in text:
                    return capability
        return Capability.UNKNOWN

    # -----------------------------
    # HEURISTIC FALLBACK
    # -----------------------------
    def _heuristic_route(self, text: str) -> Capability:

        # Execution-heavy tasks
        if any(word in text for word in ["api", "system", "backend", "service"]):
            return Capability.EXECUTION

        # Content-related tasks
        if any(word in text for word in ["post", "article", "marketing"]):
            return Capability.CONTENT_GENERATION

        # Default fallback
        return Capability.PLANNING