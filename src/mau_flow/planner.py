"""Vendor-neutral planner interface."""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlannerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["execute", "done"]
    tool_name: str | None = None
    tool_args: dict[str, Any] = Field(default_factory=dict)
    final_handoff: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> PlannerDecision:
        if self.action == "execute" and not self.tool_name:
            raise ValueError("execute decisions require tool_name")
        if self.action == "done" and self.final_handoff is None:
            raise ValueError("done decisions require final_handoff")
        return self


@runtime_checkable
class PlannerProtocol(Protocol):
    def plan(self, messages: list[dict[str, Any]]) -> PlannerDecision:
        """Return one structured action for the current local context."""
        ...

