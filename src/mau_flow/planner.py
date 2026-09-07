"""Vendor-neutral planner interface."""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, model_validator

from actunit import ActionCall

from .protocols.xml import XmlActionCodec


class PlannerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["execute", "done"]
    operation: str | None = None
    tool_call: ActionCall | None = None
    final_handoff: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> PlannerDecision:
        if self.action == "execute" and not self.operation and self.tool_call is None:
            raise ValueError("execute decisions require an XML-like operation")
        if self.action == "done" and self.final_handoff is None:
            raise ValueError("done decisions require final_handoff")
        if self.action == "done" and (self.operation is not None or self.tool_call is not None):
            raise ValueError("done decisions cannot contain a tool call")
        if self.operation and self.tool_call:
            encoded = XmlActionCodec.encode_operation(
                self.tool_call.name, **self.tool_call.arguments
            )
            if XmlActionCodec.parse_operation(self.operation) != XmlActionCodec.parse_operation(
                encoded
            ):
                raise ValueError("operation and tool_call disagree")
        return self

    def parse_operation(self) -> tuple[str, dict[str, Any]]:
        """Parse ``<read>...</read>`` style operations."""

        if self.action != "execute":
            raise ValueError("decision does not contain an operation")
        if not self.operation and self.tool_call is not None:
            return self.tool_call.name, self.tool_call.arguments
        if not self.operation:
            raise ValueError("decision does not contain an operation")
        return XmlActionCodec.parse_operation(self.operation)

    def to_tool_call(self) -> ActionCall:
        if self.action != "execute":
            raise ValueError("decision does not contain an operation")
        if self.tool_call is not None:
            return self.tool_call
        name, arguments = self.parse_operation()
        return ActionCall(name=name, arguments=arguments)

    @classmethod
    def execute(cls, name: str, **arguments: Any) -> PlannerDecision:
        """Build a structured call and retain XML for older callers."""
        return cls(
            action="execute",
            tool_call=ActionCall(name=name, arguments=arguments),
            operation=XmlActionCodec.encode_operation(name, **arguments),
        )


@runtime_checkable
class PlannerProtocol(Protocol):
    def plan(self, messages: list[dict[str, Any]]) -> PlannerDecision:
        """Return one structured action for the current local context."""
        ...
