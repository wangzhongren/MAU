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
    with pytest.raises(ValidationError, match="tool_name"):
        PlannerDecision(action="execute")
    with pytest.raises(ValidationError, match="final_handoff"):
        PlannerDecision(action="done")

