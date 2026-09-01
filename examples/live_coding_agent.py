"""Live model test using .env: python examples/live_coding_agent.py"""

from pathlib import Path

from mau_flow import MAU, BaseHandoff, CommandRequest, OpenAIPlanner, SharedExecutor, Status


def approve_shell(request: CommandRequest) -> bool:
    command = " ".join((str(request.executable), *request.args))
    return input(f"Allow shell command {command!r} for {request.reason!r}? [y/N] ").lower() == "y"

workspace = Path(__file__).parents[1] / "workspace" / "live_agent"
executor = SharedExecutor(workspace, shell_approver=approve_shell)
agent = MAU(
    name="coding-agent",
    system_prompt=(
        "Build word_stats.py in the workspace. It must accept a text-file path, print JSON "
        "containing line_count, word_count, and character_count, and use UTF-8. Inspect the "
        "workspace first. Create or update the implementation as appropriate, create a sample "
        "input, then run the CLI with shell and verify its exact counts before finishing."
    ),
    handoff_schema=BaseHandoff,
    executor=executor,
    planner=OpenAIPlanner(),
    allowed_tools={"create", "read", "update", "shell"},
)

result = agent.run(
    BaseHandoff(summary="Implement and verify the text statistics CLI", status=Status.SUCCESS),
    max_rounds=10,
)
print(result.model_dump_json(indent=2))
