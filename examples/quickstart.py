"""Offline example: python examples/quickstart.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mau_flow import MAU, BaseHandoff, Pipeline, PlannerDecision, SharedExecutor, Status


class ScriptedPlanner:
    def __init__(self, decisions):
        self.decisions = iter(decisions)

    def plan(self, _messages):
        return next(self.decisions)


workspace = Path(__file__).parents[1] / "workspace"
executor = SharedExecutor(workspace)
planner = ScriptedPlanner(
    [
        PlannerDecision(
            action="execute",
            tool_name="write_file",
            tool_args={"path": "hello.py", "content": "print('hello from MAU')\n"},
        ),
        PlannerDecision(
            action="done",
            final_handoff={
                "summary": "Generated hello.py",
                "status": "SUCCESS",
                "decisions": ["Used a deterministic scripted planner"],
            },
        ),
    ]
)

coder = MAU(
    name="coder",
    system_prompt="Create the requested file, then return a typed handoff.",
    handoff_schema=BaseHandoff,
    executor=executor,
    planner=planner,
    allowed_tools={"write_file"},
)

pipeline = Pipeline().add_mau(coder)
result = pipeline.execute(
    BaseHandoff(summary="Create a hello program", status=Status.SUCCESS),
    start="coder",
)
print(result.model_dump_json(indent=2))

