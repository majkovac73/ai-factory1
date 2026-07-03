from enum import Enum


class AgentRole(str, Enum):
    """
    Defines the persona/voice an LLM-backed agent should adopt when
    generating or revising content for a given task type.
    """
    COPYWRITER = "copywriter"
    SEO_COPYWRITER = "Etsy marketing copywriter"
    PROMPT_ENGINEER = "prompt engineer"
    RESEARCH_ANALYST = "research analyst"


# Maps a task's `type` field to the AgentRole it should be handled by.
# This is the single source of truth for role assignment — agents and
# the task processor should import from here instead of hardcoding
# role strings locally.
TASK_TYPE_ROLE_MAP = {
    "seo_writing": AgentRole.SEO_COPYWRITER,
    "image_prompt": AgentRole.PROMPT_ENGINEER,
    "research": AgentRole.RESEARCH_ANALYST,
}

DEFAULT_ROLE = AgentRole.COPYWRITER


def get_role_for_task_type(task_type: str) -> str:
    """
    Resolve a task_type string to its assigned role's string value.
    Falls back to DEFAULT_ROLE if the task_type isn't mapped.
    """
    role = TASK_TYPE_ROLE_MAP.get(task_type, DEFAULT_ROLE)
    return role.value