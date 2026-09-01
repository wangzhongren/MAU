"""Vendor-neutral planner interface."""

from __future__ import annotations

from collections.abc import Sequence
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
        if name == "read":
            paths: list[str] = []
            for element in root:
                if element.tag != "path" or element.attrib or len(element):
                    raise ValueError("read operands must be repeated text-only <path> elements")
                paths.append(element.text or "")
            if not paths:
                raise ValueError("read requires at least one path")
            return name, {"path": paths[0]} if len(paths) == 1 else {"paths": paths}
        arguments: dict[str, Any] = {}
        for element in root:
            if element.attrib or len(element):
                raise ValueError(f"opcode operand must contain text only: {element.tag}")
            if root.tag == "shell" and element.tag == "args":
                raise ValueError("shell arguments must use repeated <arg> operands")
            argument_name = "args" if root.tag == "shell" and element.tag == "arg" else element.tag
            if argument_name == "args":
                arguments.setdefault("args", []).append(element.text or "")
                continue
            if argument_name in arguments:
                raise ValueError(f"duplicate opcode operand: {element.tag}")
            arguments[argument_name] = element.text or ""
        return name, arguments

    @classmethod
    def execute(cls, name: str, **arguments: Any) -> PlannerDecision:
        """Build a correctly escaped MAU-ISA instruction."""

        if not name.strip():
            raise ValueError("opcode must not be empty")
        root = ElementTree.Element(name)
        for key, value in arguments.items():
            if name == "read" and key == "paths" and isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                if not value or not all(isinstance(item, str) for item in value):
                    raise ValueError("read paths must be non-empty strings")
                for item in value:
                    element = ElementTree.SubElement(root, "path")
                    element.text = item
                continue
            if name == "shell" and key == "args" and isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                if not all(isinstance(item, str) for item in value):
                    raise ValueError("shell args must be strings")
                for item in value:
                    element = ElementTree.SubElement(root, "arg")
                    element.text = item
                continue
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
