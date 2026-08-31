import pytest
from pydantic import ValidationError

from mau_flow import ArtifactRef, BaseHandoff, PlannerDecision, Status


def test_contracts_reject_invalid_hash_size_and_extra_fields():
    with pytest.raises(ValidationError):
        ArtifactRef(uri="file:///x", sha256="bad", size_bytes=0)
    with pytest.raises(ValidationError):
        ArtifactRef(uri="file:///x", sha256="0" * 64, size_bytes=-1)
    with pytest.raises(ValidationError):
        BaseHandoff(summary="ok", status=Status.SUCCESS, unexpected=True)


def test_handoff_requires_nonempty_summary():
    with pytest.raises(ValidationError):
        BaseHandoff(summary="", status=Status.SUCCESS)


def test_planner_decision_requires_action_payload():
    with pytest.raises(ValidationError, match="operation"):
        PlannerDecision(action="execute")
    with pytest.raises(ValidationError, match="final_handoff"):
        PlannerDecision(action="done")


def test_mau_isa_instruction_roundtrip_and_validation():
    decision = PlannerDecision.execute("create", path="a.txt", content="<hello>&")
    assert decision.parse_operation() == ("create", {"path": "a.txt", "content": "<hello>&"})
    assert decision.operation.startswith("<create>")
    malformed = PlannerDecision(action="execute", operation="<read>")
    with pytest.raises(ValueError, match="invalid operation"):
        malformed.parse_operation()


def test_mau_isa_rejects_duplicate_and_nested_operands():
    duplicate = PlannerDecision(
        action="execute",
        operation="<read><path>a</path><path>b</path></read>",
    )
    with pytest.raises(ValueError, match="duplicate"):
        duplicate.parse_operation()
    nested = PlannerDecision(
        action="execute",
        operation="<read><path><value>a</value></path></read>",
    )
    with pytest.raises(ValueError, match="text only"):
        nested.parse_operation()
