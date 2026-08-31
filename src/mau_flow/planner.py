"""Vendor-neutral planner interface."""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, model_validator


class PlannerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["execute", "done"]
    operation: str | None = None
    final_handoff: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> PlannerDecision:
        if self.action == "execute" and not self.operation:
            raise ValueError("execute decisions require an XML-like operation")
        if self.action == "done" and self.final_handoff is None:
            raise ValueError("done decisions require final_handoff")
        return self

    def parse_operation(self) -> tuple[str, dict[str, Any]]:
        """Parse ``<read>...</read>`` style operations."""

        if self.action != "execute" or not self.operation:
            raise ValueError("decision does not contain an operation")
        try:
            root = ElementTree.fromstring(self.operation)
        except ElementTree.ParseError as exc:
            raise ValueError(f"invalid operation: {exc}") from exc
        if root.attrib:
            raise ValueError("operation root must not have attributes")
        name = root.tag
        arguments: dict[str, Any] = {}
        for element in root:
            if element.tag in arguments:
                raise ValueError(f"duplicate opcode operand: {element.tag}")
            if element.attrib or len(element):
                raise ValueError(f"opcode operand must contain text only: {element.tag}")
            arguments[element.tag] = element.text or ""
        return name, arguments

    @classmethod
    def execute(cls, name: str, **arguments: Any) -> PlannerDecision:
        """Build a correctly escaped MAU-ISA instruction."""

        if not name.strip():
            raise ValueError("opcode must not be empty")
        root = ElementTree.Element(name)
        for key, value in arguments.items():
            if not key or not isinstance(value, (str, int, float, bool)):
                raise ValueError("opcode operands must have names and scalar values")
            element = ElementTree.SubElement(root, key)
            element.text = str(value)
        return cls(action="execute", operation=ElementTree.tostring(root, encoding="unicode"))


@runtime_checkable
class PlannerProtocol(Protocol):
    def plan(self, messages: list[dict[str, Any]]) -> PlannerDecision:
        """Return one structured action for the current local context."""
        ...
