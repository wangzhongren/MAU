import pytest

from actunit import ActionCall
from mau_flow import MAU, BaseHandoff, PlannerDecision, SharedExecutor, Status


def test_builtin_errors_are_structured_and_correlated(tmp_path):
    executor = SharedExecutor(tmp_path)
    result = executor.execute_call(
        ActionCall(id="missing", name="read", arguments={"path": "x"}),
        allowed_tools=frozenset({"read"}),
    )
    assert result.call_id == "missing"
    assert result.error.code == "FILE_NOT_FOUND"
    assert result.status == "error"


def test_mau_accepts_native_call_without_xml(tmp_path):
    (tmp_path / "input.txt").write_text("payload")

    class Planner:
        def __init__(self):
            self.calls = 0

        def plan(self, messages):
            self.calls += 1
            if self.calls == 1:
                return PlannerDecision(
                    action="execute",
                    tool_call=ActionCall(id="read-1", name="read", arguments={"path": "input.txt"}),
                )
            result = next(m["content"] for m in messages if m["role"] == "tool")
            assert result["content"] == "payload"
            assert result["path"] == "input.txt"
            return PlannerDecision(
                action="done", final_handoff={"summary": "read", "status": "SUCCESS"}
            )

    mau = MAU(
        name="reader",
        system_prompt="read",
        handoff_schema=BaseHandoff,
        executor=SharedExecutor(tmp_path),
        planner=Planner(),
        allowed_tools={"read"},
    )
    assert (
        mau.run(BaseHandoff(summary="start", status=Status.SUCCESS), max_rounds=5).status
        == Status.SUCCESS
    )


def test_conflicting_xml_and_structured_call_is_rejected():
    with pytest.raises(ValueError, match="disagree"):
        PlannerDecision(
            action="execute",
            operation="<read><path>a</path></read>",
            tool_call=ActionCall(name="delete", arguments={"path": "a"}),
        )
