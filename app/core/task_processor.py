import json
from app.core.agents.planner import PlannerAgent
from app.core.agents.generator import GeneratorAgent
from app.core.agents.critic import CriticAgent
from app.core.agents.fixer import FixerAgent
from app.core.agents.schema_agent import SchemaAgent
from app.core.engine.retry_engine import RetryEngine
from app.db.database import SessionLocal
from app.models.task import Task

TASK_ROLES = {
    "seo_writing": "Etsy marketing copywriter",
    "image_prompt": "prompt engineer",
    "research": "research analyst"
}

QUALITY_THRESHOLD = 70
MAX_FIX_ROUNDS = 4


def process_task(task_id: int):

    db = SessionLocal()
    task = db.query(Task).filter(Task.id == task_id).first()

    task.status = "processing"
    db.commit()

    planner = PlannerAgent()
    generator = GeneratorAgent()
    critic = CriticAgent()
    fixer = FixerAgent()
    schema_agent = SchemaAgent()
    retry = RetryEngine()

    def attempt_fix(current_output, critique):
        round_output = current_output

        for attempt in range(MAX_FIX_ROUNDS):
            if isinstance(round_output, dict):
                round_output = json.dumps(round_output, ensure_ascii=False)

            fixed = fixer.improve(round_output, critique, task.type, task.input, TASK_ROLES.get(task.type, "copywriter"))
            validation = schema_agent.validate_seo(fixed)

            if validation["valid"]:
                result_data = validation["data"]
                critique = critic.review(result_data, task.type, task.input)
                if critique.get("valid") and critique.get("score", 0) >= QUALITY_THRESHOLD:
                    return {
                        "valid": True,
                        "data": result_data
                    }
                round_output = result_data
                continue

            round_output = fixed
            critique = {
                "valid": False,
                "score": 0,
                "issues": [validation.get("error", "Invalid JSON after fix")],
                "recommendation": "Attempt another revision."
            }

        return {
            "valid": False,
            "error": f"Quality loop failed after {MAX_FIX_ROUNDS} revisions: {critique.get('issues')}",
            "data": None
        }

    def run_quality_loop(candidate: str):
        validation = schema_agent.validate_seo(candidate)

        if not validation["valid"]:
            return attempt_fix(candidate, {
                "valid": False,
                "score": 0,
                "issues": [validation.get("error", "Schema validation failed")],
                "recommendation": "Fix the JSON output and meet schema requirements."
            })

        output_data = validation["data"]
        critique = critic.review(output_data, task.type, task.input)

        if critique.get("valid") and critique.get("score", 0) >= QUALITY_THRESHOLD:
            return {
                "valid": True,
                "data": output_data
            }

        return attempt_fix(output_data, critique)

    try:
        plan = planner.create_plan(task.type, task.input)
        context = task.input
        candidate = None

        for step in plan.get("steps", []):
            candidate = generator.generate_step(step, context, TASK_ROLES.get(task.type, "copywriter"), task.type)
            if candidate is None:
                raise Exception("Generator returned no output for step")
            context += "\\n" + str(candidate)

        final_output = retry.run(lambda: run_quality_loop(candidate))

        task.output = json.dumps(final_output, ensure_ascii=False)

        task.status = "done"

    except Exception as e:
        task.status = "failed"
        task.output = str(e)

    db.commit()
    db.close()
